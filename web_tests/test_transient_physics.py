"""Conservation, fixed point, topology and numerical refinement of hot dynamics.

Fixtures are deliberately synthetic thermodynamic networks, not private engine
data or claimed reconstructions of any published engine.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch
import numpy as np

from webapp.transient import transient_setup, normalize_request, calculate_transient
from transient import HotStateModel, gas_flux
from task_control import CancellationToken, TaskCancelled, cancellation_scope
from web_tests.convergence_fixture import converged_evidence


def source_fixture(cycle='gg',dual=False):
    inp=dict(cycle_type=cycle,gg_layout='dual_shaft' if dual else 'single_shaft',
             propellant_pair='lox_methane',ambient_pressure=100000.0,
             expansion_ratio=20.0,gg_exhaust_expansion_ratio=4.0,chamber_l_star_m=1.0,pc=8e6)
    components={};states={}
    def component(name,**values): components[name]={'name':name,'outputs':values}
    def state(name,p,T,m): states[name]=dict(name=name,pressure=p,temperature=T,mass_flow=m)
    def gas(name,key,p,T,m,cp,R):
        state(key,p,T,m)
        component(name,cp_Jkgk=cp,gamma=cp/(cp-R),molar_mass_kg_per_kmol=8314.462618/R)
    pc=8e6; pump_p=12e6 if cycle=='gg' else 25e6
    summary=dict(solver_residual_norm=1e-8,mdot_ox_kg_s=80.,mdot_fuel_kg_s=20.,mdot_total_kg_s=100.,thrust_N=310000.)
    for side,m,power in [('ox',80.,800000.),('fuel',20.,400000.)]:
        rpm=20000 if not dual and cycle=='gg' else (20000 if side=='ox' else 30000)
        component(side+'_pump',p_in_MPa=1.,p_out_MPa=pump_p/1e6,delta_p_Pa=pump_p-1e6,
                  power_W=power,nr_rpm=rpm)
        state('lox_after_pump' if side=='ox' else 'fuel_after_pump',pump_p,100 if side=='ox' else 120,m)
    state('fuel_after_cooling',pump_p-1e6,350.,20.)
    main_m=96. if cycle=='gg' else 100.
    gas('main_chamber','main_chamber',pc,3300.,main_m,3000.,400.)
    components['main_chamber']['outputs']['cc_volume_m3']=.02
    component('nozzle',At_m2=.02,thrust_vac_N=350000.)
    if cycle=='gg':
        summary.update(gg_mdot_ox_kg_s=1.,gg_mdot_fuel_kg_s=3.)
        gas('gas_generator','gg_outlet',10e6,900.,4.,3500.,500.)
        cp,R,T=3500.,500.,900.
        for name,m,power,rpm in ([('gg_turbine',4.,1200000/.97,20000)] if not dual else
                                 [('gg_turbine_ox',2.5,800000/.97,20000),('gg_turbine_fuel',1.5,400000/.97,30000)]):
            drop=power/m
            component(name,p_in_Pa=10e6,p_out_Pa=4e5,mass_flow_kgs=m,actual_drop_Jkg=drop,
                      turbine_power_W=power,cp_in_Jkgk=cp,gamma_in=cp/(cp-R),molar_mass_in=8314.462618/R,
                      T_exhaust_K=T-drop/cp,shaft_speed_rpm=rpm)
        state('gg_turbine_exhaust',4e5,T-1200000/.97/4/cp,4.)
        component('gg_exhaust_nozzle',At_m2=.004,thrust_vac_N=5000.)
    else:
        summary.update(mdot_ox_to_ox_preburner_kg_s=79.,mdot_fuel_to_ox_preburner_kg_s=1.,
                       mdot_ox_to_fuel_preburner_kg_s=1.,mdot_fuel_to_fuel_preburner_kg_s=19.,
                       crossflow_fuel_to_oxpb_margin_MPa=1.,crossflow_lox_to_fuelpb_margin_MPa=1.)
        for side,p,T,m,cp,R,power in [('ox',20e6,800.,80.,1500.,300.,800000),
                                     ('fuel',18e6,900.,20.,3500.,500.,400000)]:
            gas(side+'_preburner',side+'_preburner_outlet',p,T,m,cp,R)
            component(side+'_preburner_turbine',p_in_Pa=p,p_out_Pa=pc*1.2,mass_flow_kgs=m,
                      actual_drop_Jkg=power/.98/m,turbine_power_W=power/.98,
                      cp_in_Jkgk=cp,gamma_in=cp/(cp-R),molar_mass_in=8314.462618/R)
    return dict(id='synthetic',project_id='test',revision=1,inputs=deepcopy(inp),result=dict(
        calculation=dict(inputs=inp,summary=summary,states=states,components=components,convergence=converged_evidence(1e-8)),
        assessment=dict(status='pass',checks=[])))


class TransientPhysicsTests(unittest.TestCase):
    def test_all_topologies_start_at_a_true_fixed_point(self):
        for cycle,dual,shafts in [('gg',False,1),('gg',True,2),('ffsc',True,2)]:
            with self.subTest(cycle=cycle,dual=dual):
                source=source_fixture(cycle,dual);setup=transient_setup(source)
                self.assertTrue(setup['ready'],setup['issues'])
                p=setup['defaults']|dict(duration_s=.4,command_time_s=.1,command_ramp_s=.02,
                                         fuel_area_ratio=1.,ox_area_ratio=1.,sample_count=41)
                model=HotStateModel(source['result']['calculation'],p)
                self.assertEqual(len(model.shafts),shafts)
                self.assertLess(max(abs(model.rhs(0,model.initial))),1e-8)
                rows,_=model.integrate()
                self.assertEqual(len(rows),41)
                self.assertLess(max(abs(x['pc_Pa']/rows[0]['pc_Pa']-1) for x in rows),1e-10)
                if shafts==1:
                    self.assertTrue(all(abs(x['shaft_ox_rpm']-x['shaft_fuel_rpm'])<1e-8 for x in rows))

    def test_mass_energy_and_shaft_inventories_close_after_real_disturbance(self):
        source=source_fixture('gg',True);before=deepcopy(source)
        with patch('webapp.transient.science_environment',return_value={'test':True}):
            result=calculate_transient(dict(source_job_id='synthetic',duration_s=.5,command_time_s=.1,
                command_ramp_s=.02,sample_count=101,fuel_area_ratio=.97,ox_area_ratio=.95),source)
        self.assertEqual(source,before)
        self.assertTrue(result['complete'],result['termination_reason'])
        self.assertEqual(result['audits']['status'],'pass')
        self.assertLess(result['audits']['mass_relative_error'],1e-8)
        self.assertLess(result['audits']['energy_relative_error'],1e-8)
        self.assertGreater(abs(result['metrics']['final_pc_Pa']/result['metrics']['initial_pc_Pa']-1),1e-4)
        self.assertFalse(result['model_description']['experiment_validated'])
        # A signed, independent inventory change must equal the integrated external
        # boundary flux, including rotor work; absolute values would hide errors.
        first,last=result['samples'][0],result['samples'][-1]
        self.assertAlmostEqual(last['total_stored_energy_J']-first['total_stored_energy_J'],
                               last['integrated_net_energy_J'],places=4)
        self.assertGreater(abs(last['integrated_net_energy_J']),1.0)

    def test_thermal_volume_and_rotor_inertia_change_their_physical_derivatives(self):
        source=source_fixture();p=transient_setup(source)['defaults']
        model=HotStateModel(source['result']['calculation'],p)
        y=model.initial.copy();y[2*model.nv]=1.02  # extra rotor kinetic energy
        base=model.rhs(.5,y)
        heavier=HotStateModel(source['result']['calculation'],p|{'inertia_ox_kg_m2':2*p['inertia_ox_kg_m2']})
        d=heavier.rhs(.5,y)
        self.assertGreater(abs(base[2*model.nv]),1e-3)
        self.assertAlmostEqual(d[2*model.nv]*2,base[2*model.nv],places=8)
        larger=HotStateModel(source['result']['calculation'],p|{'chamber_volume_m3':2*p['chamber_volume_m3']})
        d=larger.rhs(.5,y)
        self.assertGreater(abs(base[1]),1e-3)
        self.assertAlmostEqual(d[1]*2,base[1],places=8)

    def test_time_tolerance_refinement_matches_pressure_and_speed(self):
        source=source_fixture('ffsc',True)
        p=transient_setup(source)['defaults']|dict(duration_s=.4,command_time_s=.1,
                                                  command_ramp_s=.01,sample_count=81)
        coarse,_=HotStateModel(source['result']['calculation'],p|{'rtol':1e-5}).integrate()
        fine,_=HotStateModel(source['result']['calculation'],p|{'rtol':1e-8}).integrate()
        self.assertEqual(len(coarse),len(fine))
        for key in ('pc_Pa','shaft_ox_rpm','chamber_temperature_K'):
            error=max(abs(a[key]-b[key])/abs(b[key]) for a,b in zip(coarse,fine))
            self.assertLess(error,2e-4,key)

    def test_pump_head_and_effective_restriction_are_solved_together(self):
        source=source_fixture();p=transient_setup(source)['defaults']
        model=HotStateModel(source['result']['calculation'],p)
        _,data=model.evaluate(.5,model.initial)
        for side,pump in model.pumps.items():
            predicted=pump['dp0']*((1+p['pump_head_slope'])-p['pump_head_slope']*(data['mdot'][side]/pump['mdot0'])**2)
            self.assertAlmostEqual(data['pump_pressure'][side]-pump['pin'],predicted,places=3)

    def test_instantaneous_energy_balance_from_independent_boundary_terms(self):
        source=source_fixture('gg',True);p=transient_setup(source)['defaults']
        model=HotStateModel(source['result']['calculation'],p)
        y=model.initial.copy();y[1]*=1.01;y[2*model.nv]*=1.03
        derivative,data=model.evaluate(.5,y)
        d_inventory=sum(derivative[2*i+1]*v['U0'] for i,v in enumerate(model.volumes))
        d_inventory+=sum(derivative[2*model.nv+j]*s['K0'] for j,s in enumerate(model.shafts))
        feeds=[{'ox':0.,'fuel':0.} for _ in model.volumes];boundary=0.
        for b in model.branches:
            side=b['side'];q=b['C']*model.command(.5,side)*np.sqrt(max(0,data['pump_pressure'][side]-data['gas'][b['dest']]['p']))
            feeds[b['dest']][side]+=q
            boundary+=q*model.liquid_cp[side]*b['T0']
        for i,v in enumerate(model.volumes):
            if v['heat_side']!='total': boundary+=v['heat0']*feeds[i][v['heat_side']]/v['heat_m0']
        for n in model.nozzles:
            v=model.volumes[n['cv']];g=data['gas'][n['cv']]
            q=n['C']*gas_flux(g['p'],g['T'],model.ambient,v['gamma'],v['R'])
            boundary-=q*v['cp']*g['T']
        boundary-=sum(data['pump_power'].values())
        boundary-=sum((1-s['efficiency'])*data['turbine_power'][s['side']] for s in model.shafts)
        self.assertAlmostEqual(d_inventory,boundary,places=5)
        self.assertGreater(abs(boundary),100.)

    def test_terminal_event_is_independent_of_output_sample_count(self):
        source=source_fixture();p=transient_setup(source)['defaults']|dict(duration_s=.4,
            command_time_s=.1,command_ramp_s=.01)
        endings=[]
        for count in (21,401):
            model=HotStateModel(source['result']['calculation'],p|{'sample_count':count})
            # Known continuous boundary crossing exercises event truncation
            # independently of both the adaptive steps and displayed time grid.
            model.validity_margin=lambda t,y:.137-t
            rows=[]
            with self.assertRaisesRegex(ValueError,'边界'):
                model.integrate(rows.append)
            endings.append(rows[-1]['time_s'])
            self.assertTrue(all(row['time_s']<=.137+1e-10 for row in rows))
        self.assertAlmostEqual(endings[0],.137,places=10)
        self.assertAlmostEqual(endings[0],endings[1],places=10)

    def test_tiny_nominal_power_fit_is_explicit_and_never_efficiency_above_one(self):
        source=source_fixture('ffsc',True)
        turbine=source['result']['calculation']['components']['ox_preburner_turbine']['outputs']
        turbine['turbine_power_W']=800000/(1+2.5e-8)
        turbine['actual_drop_Jkg']=turbine['turbine_power_W']/turbine['mass_flow_kgs']
        setup=transient_setup(source)
        self.assertTrue(setup['ready'],setup['issues'])
        model=HotStateModel(source['result']['calculation'],setup['defaults'])
        self.assertTrue(all(s['efficiency']<=1 for s in model.shafts))
        self.assertAlmostEqual(model.turbines[0]['nominal_work_fit_relative_correction'],2.5e-8,places=14)
        self.assertLess(max(abs(model.rhs(0,model.initial)[:-2])),1e-8)
        turbine['turbine_power_W']=800000/(1+2e-6)
        self.assertFalse(transient_setup(source)['ready'])

    def test_invalid_sources_and_requests_are_not_silently_repaired(self):
        source=source_fixture();broken=deepcopy(source)
        broken['result']['assessment']['status']='fail'
        self.assertFalse(transient_setup(broken)['ready'])
        broken=source_fixture('ffsc',True)
        broken['result']['calculation']['summary']['crossflow_fuel_to_oxpb_margin_MPa']=-1
        self.assertFalse(transient_setup(broken)['ready'])
        broken=deepcopy(source)
        broken['result']['calculation']['states']['gg_outlet']['pressure']=20e6
        self.assertFalse(transient_setup(broken)['ready'])
        for bad in [{'fuel_area_ratio':.5},{'sample_count':22.5},{'rtol':float('nan')},
                    {'duration_s':.1},{'inertia_ox_kg_m2':0},{'sample_count':True},{'fake_tau':1}]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                normalize_request({'source_job_id':'synthetic',**bad},source)

    def test_cancel_preserves_published_time_points(self):
        source=source_fixture();token=CancellationToken();published=[]
        def progress(status,partial):
            published.append(partial)
            if len(partial['samples'])>=10: token.cancel()
        with patch('webapp.transient.science_environment',return_value={'test':True}),cancellation_scope(token):
            with self.assertRaises(TaskCancelled):
                calculate_transient({'source_job_id':'synthetic'},source,progress)
        self.assertGreaterEqual(len(published[-1]['samples']),10)
        self.assertFalse(published[-1]['complete'])

    def test_nozzle_flux_has_correct_choking_and_direction(self):
        self.assertEqual(gas_flux(1e5,300,2e5,1.4,287),0.)
        choked=gas_flux(1e6,300,0,1.4,287)
        self.assertAlmostEqual(choked,gas_flux(1e6,300,2e5,1.4,287))
        self.assertLess(gas_flux(1e6,300,9e5,1.4,287),choked)
        self.assertAlmostEqual(gas_flux(1e6,1200,0,1.4,287),choked/2)


if __name__=='__main__': unittest.main()
