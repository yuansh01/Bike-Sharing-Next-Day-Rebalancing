"""Next-day forecasting and fleet-conserving, event-level Citi Bike replay."""
from __future__ import annotations
import argparse
import heapq
import json
from pathlib import Path
import platform
import numpy as np
import pandas as pd
import scipy
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

START = pd.Timestamp('2019-04-01')
SPLIT = pd.Timestamp('2019-09-01')
END = pd.Timestamp('2019-10-01')
AUG = (pd.Timestamp('2019-08-01') - START).days
SEP = (SPLIT - START).days
DAYS = (END - START).days


def prepare(root, out):
    files = sorted((root / '2019-citibike-tripdata').glob('*/*.csv'))
    if not files:
        raise ValueError('No trip CSV files found')
    aggregates, events, hashes, metadata = [], [], [], []
    audit = dict(raw_rows=0, invalid_rows=0, outside_start_period=0)
    cols = ['starttime','stoptime','start station id','end station id','bikeid',
            'start station latitude','start station longitude',
            'end station latitude','end station longitude','start station name','end station name']
    for file in files:
        print('Reading', file.name, flush=True)
        for df in pd.read_csv(file, usecols=cols, chunksize=250000):
            audit['raw_rows'] += len(df)
            for c in ['starttime','stoptime']:
                df[c] = pd.to_datetime(df[c], errors='coerce', format='mixed')
            valid = (df[['starttime','stoptime','start station id','end station id']].notna().all(axis=1)
                     & (df.stoptime > df.starttime))
            audit['invalid_rows'] += int((~valid).sum())
            df = df.loc[valid].copy()
            in_range = (df.starttime >= START) & (df.starttime < END)
            audit['outside_start_period'] += int((~in_range).sum())
            df = df.loc[in_range]
            keys = ['starttime','stoptime','start station id','end station id','bikeid']
            hashes.append(pd.util.hash_pandas_object(df[keys], index=False).to_numpy())
            for kind, prefix, time in [(0,'start','starttime'),(1,'end','stoptime')]:
                part = pd.DataFrame({'station':df[f'{prefix} station id'].astype(int),
                    'hour':((df[time]-START).dt.total_seconds() // 3600).astype(int)})
                part = part.loc[part.hour.between(0,DAYS*24-1)]
                g = part.groupby(['station','hour']).size().rename('count').reset_index()
                g['kind'] = kind
                aggregates.append(g)
                m = df.loc[df[time] < SPLIT, [f'{prefix} station id',f'{prefix} station name',
                           f'{prefix} station latitude',f'{prefix} station longitude']].copy()
                m.columns = ['station','name','lat','lon']
                metadata.append(m.drop_duplicates('station'))
            e = df.loc[df.starttime >= SPLIT, ['starttime','stoptime','start station id','end station id']].copy()
            e.columns = ['start','end','origin','destination']
            events.append(e)
    keys = np.concatenate(hashes)
    audit['duplicate_key_hashes'] = int(len(keys)-len(np.unique(keys)))
    if audit['duplicate_key_hashes']:
        raise ValueError('Duplicate trip keys detected; resolve source overlap before simulation')
    del keys, hashes
    stations = pd.concat(metadata).drop_duplicates('station').sort_values('station').reset_index(drop=True)
    stations['station'] = stations.station.astype(int)
    if not np.isfinite(stations[['lat','lon']].to_numpy()).all():
        raise ValueError('Training station coordinates are missing')
    mapping = {s:i for i,s in enumerate(stations.station)}
    counts = np.zeros((DAYS, len(stations),24,2), dtype=np.int32)
    for g in aggregates:
        g = g.loc[g.station.isin(mapping)]
        h = g.hour.to_numpy()
        np.add.at(counts, (h//24, g.station.map(mapping).to_numpy(),h%24,g.kind.to_numpy()),g['count'].to_numpy())
    trips = pd.concat(events,ignore_index=True)
    known = trips.origin.isin(mapping) & trips.destination.isin(mapping)
    audit['september_trips'] = len(trips)
    audit['excluded_unseen_station_trips'] = int((~known).sum())
    trips = trips.loc[known].sort_values(['start','end','origin','destination'],kind='stable')
    event_array = np.column_stack([(trips.start-SPLIT).dt.total_seconds(),
        (trips.end-SPLIT).dt.total_seconds(),trips.origin.map(mapping),trips.destination.map(mapping)])
    # Forecasts and simulation both use the closed, pre-September station universe.
    # Rebuild September counts from retained trips so evaluation has the same population.
    counts[SEP:] = 0
    for kind in (0,1):
        h = (event_array[:,kind]//3600).astype(int)
        keep = (h>=0)&(h<30*24)
        np.add.at(counts, (SEP+h[keep]//24,event_array[keep,2+kind].astype(int),h[keep]%24,
                          np.full(keep.sum(),kind)),1)
    audit.update(stations=len(stations), retained_september_trips=len(trips), source_files=len(files))
    stations.to_csv(out/'stations.csv',index=False)
    np.savez_compressed(out/'prepared.npz',counts=counts,events=event_array)
    (out/'data_audit.json').write_text(json.dumps(audit,indent=2))
    (out/'source_manifest.json').write_text(json.dumps([
        dict(path=str(p.relative_to(root)),bytes=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns) for p in files],indent=2))
    return counts,event_array,stations,audit


def forecast(counts, day, model):
    """All feature values precede the midnight forecast origin."""
    if model == 'lag7':
        return counts[day-7].sum(axis=1).astype(float)
    history = np.arange(max(0,day-28) if model=='recent_dow' else 0, day)
    matched = history[history%7 == day%7]
    return counts[matched].sum(axis=2).mean(axis=0)


def allocation(weights, fleet):
    """Largest-remainder integer allocation, deterministic station-order ties."""
    weights = np.maximum(np.asarray(weights,float),0)
    if weights.sum()==0:
        weights = np.ones_like(weights)
    desired = fleet*weights/weights.sum()
    result = np.floor(desired).astype(int)
    order = np.argsort(-(desired-result),kind='stable')
    result[order[:int(fleet-result.sum())]] += 1
    return result


def stock_requirements(counts, day, prediction):
    """Predicted peak hourly net outflow plus training-only intrahour buffer."""
    hist = counts[max(0,day-28):day].astype(float)
    same = np.arange(max(0,day-28),day)%7 == day%7
    profile = hist[same].mean(axis=0)
    denominator = profile.sum(axis=1,keepdims=True)
    profile = np.divide(profile,denominator,out=np.full_like(profile,1/24),where=denominator>0)
    flows = profile*prediction[:,None,:]
    # Hourly ordering is uncertain; reserve one typical peak pickup hour as buffer.
    deficit = np.maximum(0,np.cumsum(flows[:,:,0]-flows[:,:,1],axis=1).max(axis=1))
    buffer = np.quantile(hist[:,:,:,0].max(axis=2),0.9,axis=0)
    return deficit+buffer


def distances(stations):
    lat = np.radians(stations.lat.to_numpy())
    lon = np.radians(stations.lon.to_numpy())
    a = np.sin((lat[:,None]-lat)/2)**2 + np.cos(lat[:,None])*np.cos(lat)*np.sin((lon[:,None]-lon)/2)**2
    return 6371*2*np.arcsin(np.sqrt(np.clip(a,0,1)))


def rebalance(inventory, target, distance, budget):
    """Maximize target-deficit coverage, then minimize bike-km, under move cap."""
    supply = np.maximum(inventory-target,0)
    demand = np.maximum(target-inventory,0)
    donors, receivers = np.flatnonzero(supply),np.flatnonzero(demand)
    if not len(donors) or not len(receivers) or budget<=0:
        return inventory.copy(),[]
    # Fix maximum achievable transfer count, making cost the sole LP objective.
    moves = min(int(supply.sum()),int(demand.sum()),int(budget))
    ni,nj = len(donors),len(receivers)
    variables = np.arange(ni*nj)
    rows = np.concatenate([np.repeat(np.arange(ni),nj),ni+np.tile(np.arange(nj),ni)])
    matrix = coo_matrix((np.ones(2*ni*nj),(rows,np.tile(variables,2))),shape=(ni+nj,ni*nj)).tocsr()
    result = linprog(distance[np.ix_(donors,receivers)].ravel(),A_ub=matrix,
        b_ub=np.r_[supply[donors],demand[receivers]],
        A_eq=coo_matrix((np.ones(ni*nj),(np.zeros(ni*nj,dtype=int),variables)),shape=(1,ni*nj)).tocsr(),
        b_eq=[moves],bounds=(0,None),method='highs')
    if not result.success:
        raise RuntimeError(result.message)
    flow = np.rint(result.x).astype(int).reshape(ni,nj)
    if np.max(np.abs(flow.ravel()-result.x))>1e-5:
        raise AssertionError('Transportation solution unexpectedly fractional')
    updated = inventory.copy()
    updated[donors] -= flow.sum(axis=1)
    updated[receivers] += flow.sum(axis=0)
    transfers = [(int(donors[i]),int(receivers[j]),int(flow[i,j]),float(distance[donors[i],receivers[j]]))
                 for i,j in zip(*np.nonzero(flow))]
    assert updated.min()>=0 and updated.sum()==inventory.sum() and flow.sum()==moves
    return updated,transfers


def simulate(events, initial, targets, distance, budget, policy):
    """Replay linked requests; rejected trips never enter the return queue."""
    inventory = initial.copy()
    fleet = int(initial.sum())
    pending = []
    cursor = 0
    daily,station_rows,transfer_rows = [],[],[]
    for day in range(30):
        start,end = day*86400,(day+1)*86400
        # Returns at the boundary precede morning transfers and tied pickups.
        while pending and pending[0][0]<=start:
            _,_,dest = heapq.heappop(pending)
            inventory[dest]+=1
        before = inventory.copy()
        target = allocation(targets[day],int(inventory.sum()))
        transfers = []
        if policy=='forecast_rebalancing':
            inventory,transfers = rebalance(inventory,target,distance,budget)
        for origin,dest,n,km in transfers:
            transfer_rows.append(dict(day=day+1,origin_index=origin,destination_index=dest,bikes=n,distance_km=km))
        requests = np.zeros(len(inventory),dtype=int)
        failed = requests.copy()
        empty_since = np.where(inventory==0,float(start),np.nan)
        empty_seconds = np.zeros(len(inventory))
        def arrive(t,dest):
            if inventory[dest]==0:
                empty_seconds[dest] += t-empty_since[dest]
                empty_since[dest] = np.nan
            inventory[dest] += 1
        while cursor<len(events) and events[cursor,0]<end:
            t,arrival,origin,dest = events[cursor]
            origin,dest = int(origin),int(dest)
            while pending and pending[0][0]<=t:
                rt,_,rd = heapq.heappop(pending)
                arrive(rt,rd)
            requests[origin]+=1
            if inventory[origin]>0:
                inventory[origin]-=1
                if inventory[origin]==0:
                    empty_since[origin]=t
                heapq.heappush(pending,(arrival,cursor,dest))
            else:
                failed[origin]+=1
            cursor+=1
        while pending and pending[0][0]<end:
            rt,_,rd = heapq.heappop(pending)
            arrive(rt,rd)
        empty = inventory==0
        empty_seconds[empty]+=end-empty_since[empty]
        assert inventory.min()>=0 and int(inventory.sum())+len(pending)==fleet
        daily.append(dict(day=day+1,requests=int(requests.sum()),unmet=int(failed.sum()),
            served=int((requests-failed).sum()),service_level=float(1-failed.sum()/max(1,requests.sum())),
            station_availability=float(1-empty_seconds.sum()/(len(inventory)*86400)),
            bikes_moved=sum(x[2] for x in transfers),bike_km=sum(x[2]*x[3] for x in transfers),
            docked=int(inventory.sum()),in_transit=len(pending),fleet=fleet))
        for s in range(len(inventory)):
            station_rows.append(dict(day=day+1,station_index=s,requests=int(requests[s]),unmet=int(failed[s]),
                inventory_before_rebalance=int(before[s]),ending_inventory=int(inventory[s]),
                availability=float(1-empty_seconds[s]/86400)))
    return pd.DataFrame(daily),pd.DataFrame(station_rows),pd.DataFrame(transfer_rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse-prepared',action='store_true')
    parser.add_argument('--fleet-scales',type=float,nargs='+',default=[0.5,1.0,1.5])
    parser.add_argument('--move-fraction',type=float,default=0.1)
    args = parser.parse_args()
    if any(s<=0 for s in args.fleet_scales) or not 0<=args.move_fraction<=1:
        parser.error('Fleet scales must be positive; move fraction must be in [0,1]')
    root = Path(__file__).resolve().parents[1]
    out = root/'outputs'/'next_day'
    out.mkdir(parents=True,exist_ok=True)
    if args.reuse_prepared:
        manifest = json.loads((out/'source_manifest.json').read_text())
        actual = sorted((root/'2019-citibike-tripdata').glob('*/*.csv'))
        if [str(p.relative_to(root)) for p in actual] != [m['path'] for m in manifest]:
            raise ValueError('Input file list changed; rebuild cache')
        for m in manifest:
            stat = (root/m['path']).stat()
            if stat.st_size != m['bytes'] or stat.st_mtime_ns != m['mtime_ns']:
                raise ValueError('Input changed; rebuild cache')
        data = np.load(out/'prepared.npz')
        counts,events = data['counts'],data['events']
        stations = pd.read_csv(out/'stations.csv')
        audit = json.loads((out/'data_audit.json').read_text())
    else:
        counts,events,stations,audit = prepare(root,out)
    models = ['lag7','calendar_mean','recent_dow']
    scores,pred_rows = [],[]
    for period,days in [('August_validation',range(AUG,SEP)),('September_evaluation',range(SEP,DAYS))]:
        # August scores exclude stations unseen before its forecast window.
        active = counts[:AUG if period.startswith('August') else SEP].sum(axis=(0,2,3))>0
        for model in models:
            pred = np.stack([forecast(counts,d,model) for d in days])
            actual = counts[list(days)].sum(axis=2)
            for kind,label in enumerate(['pickups','returns']):
                error = pred[:,active,kind]-actual[:,active,kind]
                scores.append(dict(period=period,model=model,target=label,MAE=float(np.abs(error).mean()),
                    RMSE=float(np.sqrt((error**2).mean())),WAPE=float(np.abs(error).sum()/max(1,actual[:,active,kind].sum()))))
    metrics = pd.DataFrame(scores)
    chosen = metrics.loc[metrics.period=='August_validation'].groupby('model').MAE.mean().idxmin()
    print('Selected model:',chosen,flush=True)
    metrics.to_csv(out/'forecast_metrics.csv',index=False)
    predictions = np.stack([forecast(counts,d,chosen) for d in range(SEP,DAYS)])
    for d in range(30):
        for s,station in enumerate(stations.station):
            pred_rows.append(dict(date=str((SPLIT+pd.Timedelta(days=d)).date()),station_id=int(station),
                predicted_pickups=predictions[d,s,0],predicted_returns=predictions[d,s,1],
                actual_pickups=int(counts[SEP+d,s,:,0].sum()),actual_returns=int(counts[SEP+d,s,:,1].sum())))
    pd.DataFrame(pred_rows).to_csv(out/'daily_predictions.csv',index=False)
    # Initialization uses August event-count profiles only; no September inventory fitting.
    aug = counts[AUG:SEP].astype(float)
    deficits = np.maximum(0,np.cumsum(aug[:,:,:,0]-aug[:,:,:,1],axis=2).max(axis=2))
    initial_weights = np.quantile(deficits+aug[:,:,:,0].max(axis=2),0.9,axis=0)
    reference_fleet = max(1,int(np.ceil(initial_weights.sum())))
    targets = np.stack([stock_requirements(counts,d,predictions[d-SEP]) for d in range(SEP,DAYS)])
    distance = distances(stations)
    summaries,all_daily,top_station_rows = [],[],[]
    top_indices = np.argsort(-counts[:SEP].sum(axis=(0,2,3)),kind='stable')[:15]
    top_ids = set(stations.station.iloc[top_indices])
    stations.iloc[top_indices].to_csv(out/'top15_stations.csv',index=False)
    for scale in args.fleet_scales:
        fleet = max(1,int(round(reference_fleet*scale)))
        initial = allocation(initial_weights,fleet)
        pd.DataFrame({'station_id':stations.station,'initial_bikes':initial,'training_weight':initial_weights}).to_csv(
            out/f'initial_inventory_{scale:g}.csv',index=False)
        for policy in ['no_rebalancing','forecast_rebalancing']:
            print(f'Simulating fleet scale {scale:g}: {policy} ({fleet} bikes)',flush=True)
            daily,station_results,transfers = simulate(events,initial,targets,distance,int(fleet*args.move_fraction),policy)
            daily['fleet_scale'],daily['policy'] = scale,policy
            all_daily.append(daily)
            station_results['station_id'] = stations.station.to_numpy()[station_results.pop('station_index')]
            station_results.to_csv(out/f'station_results_{scale:g}_{policy}.csv',index=False)
            top = station_results.loc[station_results.station_id.isin(top_ids)]
            top_station_rows.append(dict(fleet_scale=scale,policy=policy,requests=int(top.requests.sum()),
                unmet=int(top.unmet.sum()),service_level=float(1-top.unmet.sum()/max(1,top.requests.sum())),
                station_availability=float(top.availability.mean())))
            if len(transfers):
                transfers['origin_station'] = stations.station.to_numpy()[transfers.pop('origin_index')]
                transfers['destination_station'] = stations.station.to_numpy()[transfers.pop('destination_index')]
                transfers.to_csv(out/f'transfers_{scale:g}.csv',index=False)
            summaries.append(dict(fleet_scale=scale,fleet=fleet,policy=policy,requests=int(daily.requests.sum()),
                unmet=int(daily.unmet.sum()),service_level=float(daily.served.sum()/daily.requests.sum()),
                station_availability=float(daily.station_availability.mean()),
                bikes_moved=int(daily.bikes_moved.sum()),bike_km=float(daily.bike_km.sum())))
    summary = pd.DataFrame(summaries)
    summary.to_csv(out/'simulation_summary.csv',index=False)
    pd.DataFrame(top_station_rows).to_csv(out/'top15_summary.csv',index=False)
    daily = pd.concat(all_daily,ignore_index=True)
    daily.to_csv(out/'simulation_daily.csv',index=False)
    config = dict(selected_model=chosen,reference_fleet=reference_fleet,fleet_scales=args.fleet_scales,
                  daily_move_fraction=args.move_fraction,python=platform.python_version(),
                  numpy=np.__version__,pandas=pd.__version__,scipy=scipy.__version__,forecast_origin='00:00 America/New_York',
                  initial_in_transit=0,simulation_days=30)
    (out/'run_config.json').write_text(json.dumps(config,indent=2))
    report = ['# Next-day demand and rebalancing: executed pilot', '',
              f"Network: {audit['stations']} stations known before September; {audit['retained_september_trips']:,} retained September requests.",
              f"Excluded {audit['excluded_unseen_station_trips']:,} trips involving stations not observed before September.",
              f"Selected model: **{chosen}**, using mean pickup/return MAE on August rolling validation.",
              f"Reference fleet: **{reference_fleet:,} bikes**, a training-derived scenario, not an estimate of the historical fleet.", '',
              '| Fleet scale | Policy | Service level | Unmet requests | Bikes moved | Bike-km |',
              '|---|---|---:|---:|---:|---:|']
    for r in summaries:
        report.append(f"| {r['fleet_scale']:g} | {r['policy']} | {r['service_level']:.2%} | {r['unmet']:,} | {r['bikes_moved']:,} | {r['bike_km']:,.1f} |")
    report += ['', 'All daily fleet conservation assertions passed. Station inventories carry forward independently in each policy.', '',
        'Interpretation: this is replay of historically served trips as synthetic requests, not recovery of latent demand. '
        'Historical counts update forecasts daily in both policies, representing a shared exogenous demand-information feed. '
        'This is an optimistic information assumption if deployment observes only its own successful rides. '
        'Transfers are instantaneous at midnight, capped daily, and minimized by straight-line bike-km; '
        'these are not truck routes, road distances, or monetary costs. September was previously explored in the notebook. '
        'See METHODOLOGY.md for assumptions and limits.']
    (out/'REPORT.md').write_text('\n'.join(report)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4))
    for policy,g in summary.groupby('policy'):
        axes[0].plot(g.fleet,g.service_level*100,'o-',label=policy)
    axes[0].set(xlabel='Scenario fleet (bikes)',ylabel='Requests served (%)',title='Fleet sensitivity')
    axes[0].legend(fontsize=8)
    middle = args.fleet_scales[len(args.fleet_scales)//2]
    for policy,g in daily.loc[daily.fleet_scale==middle].groupby('policy'):
        axes[1].plot(g.day,g.service_level*100,label=policy)
    axes[1].set(xlabel='September day',ylabel='Requests served (%)',title=f'Daily service, fleet scale {middle:g}')
    fig.tight_layout()
    fig.savefig(out/'results.png',dpi=160)
    print(summary.to_string(index=False),flush=True)

if __name__=='__main__':
    main()
