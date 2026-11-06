import unittest
import numpy as np
from src.bike_project import allocation, forecast, rebalance, simulate

class BikeProjectTests(unittest.TestCase):
    def test_allocation_conserves_integer_fleet(self):
        for weights in ([0,0,0],[1,2,9]):
            for fleet in (0,1,7,101):
                result=allocation(weights,fleet)
                self.assertEqual(result.sum(),fleet)
                self.assertTrue((result>=0).all())

    def test_forecast_ignores_future(self):
        counts=np.ones((40,2,24,2),dtype=int)
        for model in ['lag7','calendar_mean','recent_dow']:
            expected=forecast(counts,30,model)
            changed=counts.copy()
            changed[30:]=99999
            np.testing.assert_equal(expected,forecast(changed,30,model))

    def test_transport_cost_budget_and_conservation(self):
        inv=np.array([4,2,0,0])
        target=np.array([0,0,3,3])
        distance=np.array([[0,1,1,10],[1,0,10,1],[1,10,0,1],[10,1,1,0]],float)
        result,flow=rebalance(inv,target,distance,3)
        self.assertEqual(sum(x[2] for x in flow),3)
        self.assertEqual(sum(x[2]*x[3] for x in flow),3)
        self.assertEqual(result.sum(),6)
        self.assertTrue((result>=0).all())
        result,flow=rebalance(inv,target,distance,6)
        np.testing.assert_equal(result,target)

    def test_failed_pickup_no_return_and_midnight_transit(self):
        events=np.array([[10,20,0,1],[11,21,0,1],[86399,86401,1,0],[86401,86402,0,1]],float)
        daily,stations,_=simulate(events,np.array([1,0]),np.ones((30,2)),np.zeros((2,2)),0,'no_rebalancing')
        self.assertEqual(daily.requests.sum(),4)
        self.assertEqual(daily.unmet.sum(),1)
        self.assertEqual(daily.iloc[0].in_transit,1)
        self.assertTrue((daily.docked+daily.in_transit==1).all())
        self.assertEqual(stations.loc[(stations.day==30)&(stations.station_index==1),'ending_inventory'].iloc[0],1)

    def test_empty_time_and_horizon_trip(self):
        events=np.array([[43200,31*86400,0,1]],float)
        daily,_,_=simulate(events,np.array([1,0]),np.ones((30,2)),np.zeros((2,2)),0,'no_rebalancing')
        self.assertAlmostEqual(daily.iloc[0].station_availability,.25)
        self.assertEqual(daily.iloc[-1].in_transit,1)
        self.assertEqual(daily.iloc[-1].docked,0)

    def test_boundary_return_precedes_rebalance_and_pickup(self):
        events=np.array([[1,86400,0,1],[86400,86401,1,0]],float)
        daily,_,_=simulate(events,np.array([1,0]),np.ones((30,2)),np.zeros((2,2)),0,'no_rebalancing')
        self.assertEqual(daily.unmet.sum(),0)
        self.assertTrue((daily.docked+daily.in_transit==1).all())

if __name__=='__main__':
    unittest.main()
