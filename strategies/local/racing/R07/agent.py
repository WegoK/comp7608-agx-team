"""Deterministic Formula rule based driver; Python 3.12, NumPy only.

init(context)/act(observation) own exactly one seat's private memory. No engine
imports, clocks, RNG or network calls occur in decisions. Initialization
reads only frozen NPZ from weights_dir. Public planning data is generated before
submission, never from
the weekend's weather seed or another participant's private state.
"""
from pathlib import Path
import json
import math

import numpy as np


def clip(value, low, high):
    return max(low, min(high, value))


class Driver:
    """Independent perception, committed tactics, reachable path and control."""

    def __init__(self, plan, controls, mechanics=None):
        self.p = plan
        self.ctrl = controls
        self.mechanics = mechanics
        self.power_pace_key = None
        self.power_commit_until = -1
        self.length, self.ds, self.count = plan['L'], plan['ds'], plan['n_nodes']
        self.tables = {}
        for compound, cold in plan['cold_caps'].items():
            self.tables[compound] = {k: np.asarray(cold[k], dtype=float) for k in
                ('temperatures', 'speeds', 'front_stiffness', 'rear_stiffness')}
            self.tables[compound]['nominal'] = np.asarray(plan['compound_speeds'][compound])
        self.road = {k: np.asarray(v) for k, v in plan['road'].items()}
        self.line = np.asarray(plan['n_line'], dtype=float)
        self.x, self.y = np.asarray(plan['line_x']), np.asarray(plan['line_y'])
        self.nx, self.ny = self.road['nx'], self.road['ny']
        self.left = np.minimum(self.road['left'], self.road['wall_left'])
        self.right = np.minimum(self.road['right'], self.road['wall_right'])
        self.corner = np.asarray(plan['corner_weight'])
        self.reference_k = self.curvature(self.x, self.y)
        self.energy_windows = self.plan_energy_windows()
        self.capabilities = {}
        if 'capabilities' in plan:
            for compound, bands in plan['capabilities']['compounds'].items():
                self.capabilities[compound] = {key: np.asarray([[band[key] for band in temperature] for temperature in bands])
                                               for key in ('ay', 'fb', 'drag', 'roll')}
        self.gg_active = False
        self.corridor_key = None
        self.phase = None
        self.pace_key = None
        self.history = {}
        self.mode = 'LINE'
        self.diagnostic = {}
        # Scalar lookup copies are bounded and retain their source object, so
        # allocator identity reuse cannot associate a new envelope with old data.
        # Path/envelope arrays are immutable after publication to the controller.
        self.scalar_tables = {}
        self.boundary_lists = (self.right.tolist(), self.left.tolist())
        self.boundary_sources = (self.right, self.left)
        self.geometry_cache = {}
        self.cache_scalar_queries = False
        self.cached_braking = None
        self.gg_scalar = {}

    def at(self, values, station):
        if self.cache_scalar_queries:
            identity = id(values)
            cached = self.scalar_tables.get(identity)
            if cached is None:
                if len(self.scalar_tables) >= 24:
                    self.scalar_tables.pop(next(iter(self.scalar_tables)))
                cached = (values, values.tolist() if isinstance(values, np.ndarray) else values)
                self.scalar_tables[identity] = cached
            values = cached[1]
        u = (station % self.length) / self.ds
        i = int(u) % self.count
        return float(values[i] + (values[(i+1) % self.count]-values[i])*(u-int(u)))

    @staticmethod
    def curvature(x, y):
        # Public coordinates are rounded to 0.1 mm at 2 m spacing. A three-node
        # derivative magnifies that quantization into false straight-line bends.
        # Ten-metre chords match the control horizon and suppress this noise.
        ax, ay = x-np.roll(x, 5), y-np.roll(y, 5)
        bx, by = np.roll(x, -5)-x, np.roll(y, -5)-y
        return 2*(ax*by-ay*bx)/np.maximum(np.hypot(ax, ay)*np.hypot(bx, by)*np.hypot(ax+bx, ay+by), 1e-8)

    def bounds(self, station, margin=None):
        margin = self.margin if margin is None else margin
        if self.cache_scalar_queries:
            # The same two immutable road arrays share one exact coordinate.
            # Outside act(), preserve direct mutable-fixture lookup semantics.
            if self.boundary_sources[0] is not self.right or self.boundary_sources[1] is not self.left:
                self.boundary_lists = (self.right.tolist(), self.left.tolist())
                self.boundary_sources = (self.right, self.left)
            right, left = self.boundary_lists
            u = (station % self.length) / self.ds
            node = int(u); i = node % self.count; j = (i+1) % self.count
            f = u-node
            return (-float(right[i]+(right[j]-right[i])*f)+margin,
                    float(left[i]+(left[j]-left[i])*f)-margin)
        return -self.at(self.right, station)+margin, self.at(self.left, station)-margin

    @staticmethod
    def bracket(temperatures, value):
        i = min(len(temperatures)-2, max(0, int(np.searchsorted(temperatures, value)-1)))
        return i, clip((value-temperatures[i])/(temperatures[i+1]-temperatures[i]), 0, 1)

    def reset(self, o):
        c = o['self']
        self.phase, self.tick, self.distance = o['phase'], None, None
        self.offset = c['n']-self.at(self.line, c['distance'])
        self.goal, self.target_car, self.defender = 0., None, None
        self.pass_progress_tick, self.pass_gap = o['tick'], float('inf')
        self.defence_goal, self.next_tactic, self.cooldown = 0., 0, 0
        self.grid_n, self.history = c['n'], {}
        self.fuel_start, self.fuel_station = c['fuel_kg'], c['distance']
        self.compounds, self.wet_race = set(), False
        self.pit_was_active, self.pit_exit_tick = False, -10000
        self.pace_key = None
        self.entry_front = None
        self.counter_car, self.counter_until = None, 0
        self.chasing, self.attack_window = False, 'NONE'
        self.energy_lap, self.energy_zone = None, None
        self.energy_floor = .04
        self.pursuit_until, self.pursuit_car = -1., None
        self.power_commit_until = -1

    def perceive(self, o):
        self.cached_braking = None
        if o['phase'] != self.phase:
            self.reset(o)
        c = o['self']
        self.o, self.c = o, c
        if self.pit_was_active and not c['pit_state']:
            self.pit_exit_tick = o['tick']
        self.pit_was_active = bool(c['pit_state'])
        self.s, self.v = c['distance'], c['speed_mps']
        self.dt = 1/30 if self.tick is None else clip((o['tick']-self.tick)/60, 1/60, .1)
        progress = self.v if self.distance is None else max(0, (self.s-self.distance)/self.dt)
        self.progress = max(progress, self.v, 1.)
        self.wet = clip(o['weather']['track_wetness']+o['weather']['rain_intensity']/60, 0, 1)
        optimum = self.p['compound_optimum'][c['compound']]
        self.thermal = clip(max((optimum-min(c['tyre_temp_c']))/45,
                               (max(c['tyre_temp_c'])-optimum)/40, 0), 0, 1)
        self.margin = 1.45+self.wet*.65*(1+self.thermal)
        self.compounds.add(c['compound'])
        self.wet_race |= o['weather']['condition'] == 'wet'
        rivals = []
        for rival in o['rivals']:
            if rival['status'] != 'racing':
                continue
            r = dict(rival)
            r['offset'] = c['n']+r['dn']-self.at(self.line, self.s+r['gap_m'])
            old = self.history.get(r['seat'])
            r['rate'] = clip((r['gap_m']-old['gap_m'])/self.dt, -50, 50) if old else r['speed_mps']-self.progress
            r['dn_rate'] = clip((r['dn']-old['dn'])/self.dt, -8, 8) if old else 0.
            r['side_rate'] = clip((r['offset']-old['offset'])/self.dt, -1, 1) if old else 0.
            r['acceleration'] = clip((r['speed_mps']-old['speed_mps'])/self.dt, -30, 10) if old else 0.
            r['pace_advantage'] = old.get('pace_advantage', 0.) if old else 0.
            exit_distance = (self.p.get('pit_exit_s', -1000)-(self.s+r['gap_m'])) % self.length
            r['merge_time'] = exit_distance/max(r['speed_mps'], 1)
            r['merging'] = (self.o['phase'] == 'race' and 'pit_exit_s' in self.p
                and exit_distance < 120 and abs(r['speed_mps']-self.p['pit_speed_mps']) < .6)
            rivals.append(r)
        self.history = {r['seat']: r for r in rivals}
        self.rivals, self.tick, self.distance = rivals, o['tick'], self.s
        self.prepare_pace()
        for r in self.rivals:
            station = self.s+r['gap_m']
            if abs(self.at(self.reference_k, station)) > .002:
                loss = clip(self.pace(station)-r['speed_mps'], -10, 10)
                r['pace_advantage'] += min(1., self.dt/3)*(loss-r['pace_advantage'])
            else:
                r['pace_advantage'] *= max(0., 1-self.dt/8)

    def road_geometry(self, reference, displacement):
        """Reuse an identical immutable road projection, never a nearby path."""
        key = (id(reference), id(self.right), id(self.left), self.margin, displacement)
        if self.cache_scalar_queries and key in self.geometry_cache:
            return self.geometry_cache[key][3:]
        lane = np.clip(reference+displacement, -self.right+self.margin, self.left-self.margin)
        for _ in range(8):
            lane = np.clip((np.roll(lane, 1)+2*lane+np.roll(lane, -1))*.25,
                           -self.right+self.margin, self.left-self.margin)
        x = self.x+(lane-self.line)*self.nx
        y = self.y+(lane-self.line)*self.ny
        curve = np.abs(self.curvature(x, y))
        if self.cache_scalar_queries:
            if len(self.geometry_cache) >= 4:
                self.geometry_cache.pop(next(iter(self.geometry_cache)))
            # Retain identities: allocator reuse cannot hit a different array.
            self.geometry_cache[key] = (reference, self.right, self.left, lane, curve)
        return lane, curve

    def prepare_pace(self):
        c, p = self.c, self.p
        key = (c['compound'], c['pit_stops'], self.o['tick']//30, int(self.wet*100),
               int(min(c['tyre_temp_c'])/2), int(max(c['tyre_temp_c'])/2))
        if key == self.pace_key:
            return
        table = self.tables[c['compound']]
        band = min(3, int(self.wet*4))
        blend = self.wet*4-band
        nominal = table['nominal']
        mass = p['dry_mass']
        load = math.sqrt((mass+p['reference_fuel'])/(mass+c['fuel_kg']))
        cold_load = math.sqrt((mass+p['cold_reference_fuel'])/(mass+c['fuel_kg']))
        speeds = (nominal[band]*(1-blend)+nominal[band+1]*blend)*load
        for temperature in (min(c['tyre_temp_c'])-2, max(c['tyre_temp_c'])+2):
            t, f = self.bracket(table['temperatures'], temperature)
            lower = table['speeds'][t, band]*(1-blend)+table['speeds'][t, band+1]*blend
            upper = table['speeds'][t+1, band]*(1-blend)+table['speeds'][t+1, band+1]*blend
            speeds = np.minimum(speeds, (lower*(1-f)+upper*f)*cold_load)
        wear = max(c['tyre_wear'])
        wear_scale = max(.45, 1-.05*wear-.4*(max(0, wear-.8)/.2)**2)
        raw = speeds*speeds
        squared = raw*self.ctrl.get(self.phase+'_margin', .985)**2*wear_scale
        # The cold tables already include observed temperature AND wet grip.
        # Keep a small forecast reserve; do not apply tyre-cold loss a second
        # time, which prevents the tyres warming and makes rain laps too slow.
        squared *= 1-.05*self.wet*self.corner
        # The actual safe line, including collision walls, has its own curvature.
        lane, curve = self.road_geometry(self.line, 0.)
        self.base_corner_curve = curve
        self.base_squared = squared.copy()
        ratio = np.clip(np.maximum(abs(self.reference_k), .0003)/np.maximum(curve, .0003), .35, 1.)
        self.gg_active = bool(self.capabilities) and self.o['phase'] == 'race'
        if self.gg_active:
            # Raw reference speeds contain acceleration/braking limits already.
            # Scaling them by k_old/k_new falsely treats a power-limited straight
            # as a fully loaded corner. Query lateral force on the actual path.
            self.prepare_capability(wear_scale)
            modes = (np.asarray(p['xmode']) > .5).astype(int)
            for _ in range(8):
                ay = self.capability_at('ay', np.sqrt(squared), modes)
                squared = np.minimum(squared, .985**2*ay/np.maximum(curve, 1e-7))
            speed = np.sqrt(squared)
            ay = speed*speed*curve
            coordinates = self.speed_coordinates(speed)
            braking = self.capability_brake(speed, ay, modes, coordinates)
            drag = self.capability_at('drag', speed, modes, coordinates)
            roll = self.capability_at('roll', speed, modes, coordinates)
            decel = .85*(braking+drag+roll)/(p['dry_mass']+c['fuel_kg'])
            budget = 2*self.ds*np.maximum(0., decel)
        else:
            squared *= ratio
            budget = np.maximum(4*self.ds, (1-.25*(self.wet>0))*np.maximum(0, raw-np.roll(raw, -1)))
        # Exact cyclic backward minimum in O(N), rather than Python per-node loops.
        limits = np.tile(squared, 2)
        cost = np.tile(budget, 2)
        cumulative = np.concatenate(([0.], np.cumsum(cost)))[:-1]
        reachable = np.minimum.accumulate((limits+cumulative)[::-1])[::-1]-cumulative
        self.envelope = np.sqrt(np.maximum(0., reachable[:self.count]))
        self.safe_line = lane
        self.pace_key = key

    def corridor_envelope(self, goal, yielding):
        """Use the same GGV budget on a stable, committed passing path.

        Raising a reference limit is justified only at nodes already limited
        by lateral grip. A power-limited straight never gets a curvature-ratio
        speed bonus. The original braking reserve, grip model, walls and every
        downstream collision/merge/pit guard remain unchanged.
        """
        c = self.c
        if (self.o['phase'] != 'race' or c['lap'] < 1 or not self.gg_active
                or c['pit_state'] or self.mode != 'PASS' or yielding
                or c['front_sliding'] or c['rear_sliding']
                or abs(goal-self.offset) > .25
                or self.tracking_error() > .4):
            return self.envelope
        # Current measured, stable displacement. Never assume a desired lane
        # change has happened, or mirror a rival's private future trajectory.
        displacement = c['n']-self.at(self.safe_line,self.s)
        raw=self.at(self.safe_line,self.s)+self.offset
        low,high=self.bounds(self.s)
        if self.projects_pass() and not low<=raw<=high:
            # A clipped current waypoint can coincide with the base line;
            # retain the committed FUTURE path instead of erasing its offset.
            displacement=self.offset
        key = (self.pace_key, round(displacement*4))
        if key != self.corridor_key:
            self.corridor_speed=self.corridor_limit(displacement)
            self.corridor_key=key
        self.diagnostic['physical_corridor']=True
        self.diagnostic['corridor_displacement']=float(displacement)
        return self.corridor_speed

    def corridor_limit(self, displacement):
        c = self.c
        lane, curve = self.road_geometry(self.safe_line, displacement)
        modes=(np.asarray(self.p['xmode'])>.5).astype(int)
        speed=np.sqrt(self.base_squared)
        ay=self.capability_at('ay',speed,modes)
        loaded=self.base_squared*self.base_corner_curve >= .95*ay
        ratio=np.where(loaded, np.maximum(1.,self.base_corner_curve/np.maximum(curve,1e-7)),1.)
        squared=self.base_squared*ratio
        # Never invent extra straight-line power or exceed the published
        # capability grid. Uphill/exit acceleration still comes from ICE/ERS.
        squared=np.minimum(squared,max(self.base_squared))
        for _ in range(8):
            ay=self.capability_at('ay',np.sqrt(squared),modes)
            squared=np.minimum(squared,.985**2*ay/np.maximum(curve,1e-7))
        speed=np.sqrt(squared)
        lateral=squared*curve
        coordinates=self.speed_coordinates(speed)
        brake=self.capability_brake(speed,lateral,modes,coordinates)
        drag=self.capability_at('drag',speed,modes,coordinates)
        roll=self.capability_at('roll',speed,modes,coordinates)
        budget=2*self.ds*.85*(brake+drag+roll)/(self.p['dry_mass']+c['fuel_kg'])
        limits=np.tile(squared,2);cost=np.tile(np.maximum(0.,budget),2)
        cumulative=np.concatenate(([0.],np.cumsum(cost)))[:-1]
        reachable=np.minimum.accumulate((limits+cumulative)[::-1])[::-1]-cumulative
        return np.sqrt(np.maximum(0.,reachable[:self.count]))

    def power_envelope(self, original, goal, yielding):
        """Price an attack's actual power, not an offline energy schedule.

        Frozen lap speeds include forward acceleration at the offline deploy
        fractions. They are not tyre/braking upper bounds when this callback
        deliberately uses a different power source. On a settled later-lap
        attack straight, use our public wheel forces and the published cap
        curve to find a power equilibrium, then enforce the SAME GGV lateral
        reserve and cyclic backward braking reserve on the real road path.
        All downstream traffic, stopping, merge, pit, fuel and thermal guards
        still take their original minimum. No hidden rival or grant is read.
        """
        c=self.c
        front=min((r for r in self.rivals if 0.<r['gap_m']<400. and not r.get('merging')),key=lambda r:r['gap_m'],default=None)
        granted=bool(c['aid_available'] or c['override_available'])
        if (self.mechanics is None or not self.gg_active or self.o['phase']!='race'
                or c['lap']<1 or c['status']!='racing' or c['pit_state'] or yielding
                or self.defender is not None or self.mode=='DEFEND' or self.wet>=.5
                or c['front_sliding'] or c['rear_sliding'] or self.v<60.
                or c['fuel_kg']<=0 or c['pu_temp_c']>115
                or abs(goal-self.offset)>.25 or self.tracking_error()>.4
                or not (front or granted or self.o['tick']<self.power_commit_until)
                or abs(self.at(self.reference_k,self.s))>=.001):
            return original
        # Finish a pass without lifting merely because the front axle cleared.
        # This lease never overrides an occupied corridor or any real guard.
        if front:
            self.power_commit_until = self.o['tick']+180
        # Respect the existing frozen straight allocation. It will establish a
        # new entry floor in the normal actuator path before the next callback.
        zone=next((i for i,(start,end,_) in enumerate(self.energy_windows) if start<=self.s%self.length<end),None)
        electric=bool(c['ers_soc_mj']>.1 and (granted or zone is not None and self.energy_zone==zone and c['ers_soc_mj']>self.energy_floor))
        overtake=bool(c['override_available'] and electric)
        aid=bool(c['aid_available'])
        vx=max(c['vx'],1.)
        ice=c['drive_force_n'][1]*vx
        normal=float(np.interp(vx*3.6,self.mechanics['speed_kph'],self.mechanics['normal_w']))
        # The public force vector uses the NORMAL cap even when override is
        # ready. Above its cutoff the vector's electric difference is zero;
        # that must not erase the still-legal override cap up to 355 km/h.
        coefficient=(self.mechanics['wheel_efficiency'] if normal<=0. else
                     min(self.mechanics['wheel_efficiency'],max(0.,(c['drive_force_n'][3]-c['drive_force_n'][1])*vx)/normal))
        displacement=c['n']-self.at(self.safe_line,self.s)
        raw=self.at(self.safe_line,self.s)+self.offset
        if self.projects_pass() and not self.bounds(self.s)[0]<=raw<=self.bounds(self.s)[1]:
            displacement=self.offset
        grade=max(0.,max(row[2] for row in self.o['preview']['rows']))
        key=(grade,self.pace_key,self.o['tick']//15,int((self.s%self.length)/50.),round(displacement*4),int(ice/10000.),electric,overtake,aid)
        if key!=self.power_pace_key:
            grid=np.asarray(self.p['capabilities']['speeds'],dtype=float)
            def excess(speed):
                curve='override_w' if overtake else 'normal_w'
                motor=float(np.interp(speed*3.6,self.mechanics['speed_kph'],self.mechanics[curve]))*coefficient if electric else 0.
                drag=float(self.capability_at('drag',speed,1))*(.95 if aid else 1.)
                roll=float(self.capability_at('roll',speed,1))
                # Only published, currently visible uphill grade is charged.
                # The target is an attainable ceiling, not a promised rollout.
                resistance=drag+roll+(self.p['dry_mass']+c['fuel_kg'])*9.81*grade
                return ice+motor-resistance*speed
            low,high=float(grid[0]),float(grid[-1])
            if excess(low)<=0:
                return original
            for _ in range(16):
                middle=(low+high)*.5
                if excess(middle)>0:low=middle
                else:high=middle
            ceiling=low
            lane, curvature = self.road_geometry(self.safe_line, displacement)
            modes=(np.asarray(self.p['xmode'])>.5).astype(int)
            # Lift only straight forward-power limits. Bends and the future
            # braking endpoint retain their original raw reference ceilings.
            forward=(np.arange(self.count)*self.ds-self.s)%self.length
            mask=(modes==1)&(curvature<.001)&((forward<900.)|(forward>self.length-self.ds))
            squared=np.where(mask,np.maximum(self.base_squared,ceiling*ceiling),self.base_squared)
            grip=.98 if aid else 1.
            for _ in range(24):
                ay=self.capability_at('ay',np.sqrt(squared),modes)*grip
                lateral_limit=np.minimum(squared,.985**2*ay/np.maximum(curvature,1e-7))
                delta=float(np.max(squared-lateral_limit))
                squared=lateral_limit
                if delta<1e-9:
                    break
            # Re-evaluate the braking force at the resulting speed. Freezing
            # drag/downforce at the original raised ceiling overstates the
            # braking cost after a backward speed reduction.
            limited=squared.copy()
            for _ in range(12):
                speed=np.sqrt(limited)
                coordinates=self.speed_coordinates(speed)
                braking=self.capability_brake(speed,limited*curvature/grip,modes,coordinates)*grip
                drag=self.capability_at('drag',speed,modes,coordinates)*(np.where(modes==1,.95,1.) if aid else 1.)
                roll=self.capability_at('roll',speed,modes,coordinates)
                budget=2*self.ds*.85*(braking+drag+roll)/(self.p['dry_mass']+c['fuel_kg'])
                limits=np.tile(limited,2);cost=np.tile(np.maximum(0.,budget),2)
                cumulative=np.concatenate(([0.],np.cumsum(cost)))[:-1]
                reachable=np.minimum.accumulate((limits+cumulative)[::-1])[::-1]-cumulative
                next_limit=np.minimum(limited,np.maximum(0.,reachable[:self.count]))
                delta=float(np.max(limited-next_limit))
                limited=next_limit
                if delta<1e-7:
                    break
            self.power_pace=np.sqrt(limited)
            self.power_pace_key=key;self.power_ceiling=ceiling
        full_attack=self.at(self.power_pace,self.s+self.v*self.ctrl['latency'])>self.at(original,self.s+self.v*self.ctrl['latency'])+.3
        if full_attack:
            self.chasing=True  # Same actor's intentional attack uses the priced full deploy mode.
        self.diagnostic.update(dynamic_power_plan=True,power_equilibrium_mps=float(self.power_ceiling),power_ice_w=float(ice),power_electric_allowed=electric,power_path_displacement=float(displacement),power_full_attack=bool(full_attack))
        return self.power_pace


    def prospective_corridor(self, goal):
        """Rank a hypothetical path with the SAME GGV equations as control.

        This never pretends the lane change has already happened. It is used
        only in tactical forecasts; actual speed control still requires the
        original measured stable-lane/physical tracking conditions.
        """
        cache=getattr(self,'approach_envelopes',{})
        key=(self.pace_key,round(goal*4))
        if key not in cache:
            cache={k:v for k,v in cache.items() if k[0]==self.pace_key}
            if len(cache)>6:
                cache={}
            cache[key]=self.corridor_limit(goal)
            self.approach_envelopes=cache
        return cache[key]

    def prepare_capability(self, wear_scale):
        """Interpolate frozen public GGV forces at this seat's observed state."""
        table = self.capabilities[self.c['compound']]
        temperatures = self.tables[self.c['compound']]['temperatures']
        w = min(3, int(self.wet*4)); fw = self.wet*4-w
        self.gg = {}
        self.gg_scalar = {}
        for key, values in table.items():
            extremes = []
            for temperature in (min(self.c['tyre_temp_c'])-2, max(self.c['tyre_temp_c'])+2):
                t, ft = self.bracket(temperatures, temperature)
                low = values[t, w]*(1-fw)+values[t, w+1]*fw
                high = values[t+1, w]*(1-fw)+values[t+1, w+1]*fw
                value = low*(1-ft)+high*ft
                if key in ('ay', 'fb'):
                    # Do not silently clamp a measured temperature below the
                    # precomputed grid and pretend it has the boundary grip.
                    model = self.p['capabilities'].get('thermal_model', dict(cold_width=45., hot_width=40., floor=.45))
                    optimum = self.p['compound_optimum'][self.c['compound']]
                    def window(temp):
                        width = model['cold_width'] if temp < optimum else model['hot_width']
                        return max(model['floor'], 1-.5*((temp-optimum)/width)**2)
                    bounded = clip(temperature, temperatures[0], temperatures[-1])
                    value *= min(1., window(temperature)/window(bounded))
                extremes.append(value)
            self.gg[key] = np.minimum(extremes[0], extremes[1])
            if key in ('ay', 'fb'):
                self.gg[key] *= wear_scale
        mass = self.p['dry_mass']+self.c['fuel_kg']
        self.gg['ay'] *= self.p['capabilities']['reference_mass_kg']/mass

    def speed_coordinates(self, speed):
        """Compute unchanged grid coordinates once for each force query batch."""
        grid = self.p['capabilities']['speeds']
        if isinstance(speed, (float, int)):
            u = clip((speed-grid[0])/(grid[1]-grid[0]), 0, len(grid)-1)
            i = min(int(u), len(grid)-2)
            return i, u-i
        u = ((np.asarray(speed)-grid[0])/(grid[1]-grid[0])).clip(0, len(grid)-1)
        i = np.minimum(u.astype(int), len(grid)-2); f = u-i
        return i, f

    def capability_at(self, key, speed, mode, coordinates=None):
        """Vector interpolation of physical capacity, with bounded grid ends."""
        i, f = self.speed_coordinates(speed) if coordinates is None else coordinates
        values = self.gg[key]
        if self.cache_scalar_queries and isinstance(i, int) and isinstance(mode, int):
            if key not in self.gg_scalar:
                self.gg_scalar[key] = values.tolist()
            row = self.gg_scalar[key][mode]
            return row[i]*(1-f)+row[i+1]*f
        return values[mode, i]*(1-f)+values[mode, i+1]*f

    def capability_brake(self, speed, lateral, mode, coordinates=None):
        """Remaining longitudinal tyre force on the public friction ellipse."""
        coordinates = self.speed_coordinates(speed) if coordinates is None else coordinates
        capacity = self.capability_at('ay', speed, mode, coordinates)
        if self.cache_scalar_queries and isinstance(coordinates[0], int) and isinstance(mode, int) and isinstance(lateral, (float, int)):
            q = math.sqrt(clip(1-lateral/max(capacity, 1e-6), 0, 1))
            columns = self.gg['fb'].shape[-1]
            u = clip(q*(columns-1), 0, columns-1)
            j = min(int(u), columns-2); f = u-j
            i, fv = coordinates
            if 'fb' not in self.gg_scalar:
                self.gg_scalar['fb'] = self.gg['fb'].tolist()
            values = self.gg_scalar['fb'][mode]
            low = values[i][j]*(1-f)+values[i][j+1]*f
            high = values[i+1][j]*(1-f)+values[i+1][j+1]*f
            return low*(1-fv)+high*fv
        q = np.sqrt(np.clip(1-np.asarray(lateral)/np.maximum(capacity, 1e-6), 0, 1))
        columns = self.gg['fb'].shape[-1]
        u = np.clip(q*(columns-1), 0, columns-1)
        j = np.minimum(u.astype(int), columns-2); f = u-j
        i, fv = coordinates
        values = self.gg['fb']
        low = values[mode,i,j]*(1-f)+values[mode,i,j+1]*f
        high = values[mode,i+1,j]*(1-f)+values[mode,i+1,j+1]*f
        return low*(1-fv)+high*fv

    def brake_capacity(self):
        """Predict this car's usable braking, including current lateral load."""
        if not self.gg_active:
            return None
        if self.cached_braking is not None:
            return self.cached_braking
        lateral = abs(self.c.get('g_lat', 0))*9.81
        mode = self.c.get('aero_mode', 0)
        force = float(self.capability_brake(self.v, lateral, mode))
        self.cached_braking = .85*(force+self.c['drag_n']+self.c['rolling_resistance_n'])/(self.p['dry_mass']+self.c['fuel_kg'])
        return self.cached_braking

    def pace(self, station):
        return self.at(self.envelope, station)

    def lane(self, station, goal, seconds):
        offset = self.offset+clip(goal-self.offset, -1.5*seconds, 1.5*seconds)
        low, high = self.bounds(station)
        return clip(self.at(self.safe_line, station)+offset, low, high)

    def projects_pass(self):
        """Whether a steady later-lap pass uses pointwise road projection."""
        return (self.o['phase']=='race' and self.c['lap']>=1 and self.mode=='PASS'
                and not self.c['pit_state'] and self.wet<.5 and self.defender is None
                and not self.c['front_sliding'] and not self.c['rear_sliding'])

    def tracking_error(self):
        """Measure against the same reachable waypoint as lane()/steering."""
        target=self.at(self.safe_line,self.s)+self.offset
        recovering = (self.o['phase']=='race' and self.c['lap']>=1
                      and not self.c['pit_state'] and self.mode in
                      ('LINE','DRAFT','CLEAR','RESET_ATTACK','CLEAR_BOTTLENECK'))
        if self.projects_pass() or recovering:
            # Steering has always projected this reference onto road bounds.
            # Measure that reachable waypoint after a pass, without resetting
            # the rate-limited reference or changing its steering trajectory.
            target=clip(target,*self.bounds(self.s))
        return abs(self.c['n']-target)

    def committed_close(self, front):
        """Keep an established passing side while the pursuit gap is attained.

        This is the existing tactical1.2s window, using published vx. Official
        readiness still comes exclusively from the engine's one-second latch.
        """
        return bool(front and self.o['phase']=='race' and self.c['lap']>=1
                    and self.mode=='PASS' and not self.c['pit_state']
                    and 0<front['gap_m'] and (front['gap_m']<=1.2*max(self.c['vx'],1.)
                        or front['gap_m']<=90. and self.catch_window(front,1.2) is not None))

    def rival_lane(self, rival, station, seconds):
        """Predict legal lateral occupancy, including the public pit exit."""
        if rival.get('merging') and seconds >= rival['merge_time']-.2:
            return 0.
        low, high = self.bounds(station)
        return clip(self.at(self.line, station)+rival['offset']+rival['side_rate']*min(seconds, .5), low, high)

    def opportunity(self, front):
        """Recognize public exit, braking and aid opportunities on every lap."""
        v = max(self.v, 20.)
        speeds = [self.pace(self.s+distance) for distance in (30, 90, 180, 300)]
        braking = min(speeds[1:]) < speeds[0]-8
        exit_gain = max(speeds[1:])-speeds[0] > 8
        straight = all(abs(row[1]) < .002 for row in self.o['preview']['rows'][:8])
        closing = self.progress-front['speed_mps']
        history_gain = -front['rate']
        fresh = (self.c['pit_stops'] > 0 and max(self.c['tyre_wear']) < .1
                 and max(abs(t-self.p['compound_optimum'][self.c['compound']]) for t in self.c['tyre_temp_c']) < 20
                 and front.get('pace_advantage', 0) > .8)
        advantage = max(closing, history_gain, max(speeds)-front['speed_mps'],
                        front.get('pace_advantage', 0) if fresh else 0)
        counter = front['seat'] == self.counter_car and self.o['tick'] < self.counter_until
        window = ('COUNTER' if counter else 'BRAKE' if braking else 'EXIT' if exit_gain else 'FRESH_TYRE' if fresh else 'AID' if
                  self.c['aid_available'] or self.c['override_available'] else 'STRAIGHT' if straight else 'NONE')
        self.diagnostic['opportunity'] = window
        return advantage, window, front['gap_m'] < min(100, max(50, 18+max(advantage, 0)*6))

    def catch_window(self, front, headway=0.):
        """Forecast a reachable pass, allowing a visible leader to accelerate.

        The old preview froze a slow corner-exit leader at its current speed
        for five seconds. That predicted imaginary queues and pulled the car
        off its best line for attacks it could not finish. This forecast uses
        only observed speed/acceleration, the public circuit and our own drive
        force. It selects tactics; the independent emergency guard is unchanged.
        """
        own_s,other_s=self.s,self.s+front['gap_m']
        own_v,other_v=self.v,front['speed_mps']
        mass=self.c['effective_mass_kg']
        drive=self.c['drive_force_n'][3 if self.c['ers_soc_mj']>.1 else 1]
        own_accel=clip((drive-self.c['drag_n']-self.c['rolling_resistance_n'])/mass, .5, 10.)
        # An observed exit acceleration persists; otherwise allow an ordinary
        # 3m/s² return to pace instead of assuming that the leader stays slow.
        exit_accel=clip(max(3.,front.get('acceleration',0.)),3.,10.)
        loss=max(0.,front.get('pace_advantage',0.))
        for step in range(1,31):
            t=step*.2
            own_limit=self.pace(own_s)
            other_limit=max(10.,self.pace(other_s)-loss)
            new_own=own_v+clip(own_limit-own_v,-20.*.2,own_accel*.2)
            new_other=other_v+clip(other_limit-other_v,-30.*.2,exit_accel*.2)
            own_s+=(own_v+new_own)*.1;other_s+=(other_v+new_other)*.1
            own_v,other_v=new_own,new_other
            if other_s-own_s <= max(8.,new_own*headway):
                return t
        return None

    def pursuit_zone(self, front):
        """Public upcoming detector, never an inferred official grant.

        The old four-second utility priced only immediate progress. A safe
        preparation that pays after detection/activation therefore lost to
        staying. Metadata is immutable published track/rule data; every rival
        quantity below comes from this seat's current legal observation.
        """
        c = self.c
        if (self.mechanics is None or self.o['phase'] != 'race' or c['lap'] < 1
                or c['pit_state'] or c['status'] != 'racing' or self.defender is not None
                or c['front_sliding'] or c['rear_sliding'] or self.wet >= .5
                or front.get('merging') or not 8. < front['gap_m'] < 400.):
            return None
        choices = []
        for kind, detector, activation, end in self.mechanics['zones']:
            if kind == 0 and (self.wet > 0 or self.o['weather']['rain_intensity'] > 0):
                continue
            if kind == 1 and c['ers_soc_mj'] <= .1:
                continue
            distance = (detector-self.s) % self.length
            if 0 < distance <= min(900., 10.*max(self.v, 30.)):
                choices.append((distance, int(kind), activation, end))
        if not choices:
            return None
        distance, kind, activation, end = min(choices)
        return kind, distance, (activation-(self.s+distance)) % self.length, (end-activation) % self.length

    def qualification_value(self, front, goal):
        """Forecast benefit at a detector beyond the old four-second horizon.

        Simulated following speed is actually capped when the original stopping
        geometry conflicts. Side separation is NOT a licence to skip the real
        control guard. Prediction uses vx for time headway; 30Hz inputs cannot
        certify the engine's interpolated 60Hz one-second detection event.
        """
        zone = self.pursuit_zone(front)
        if zone is None:
            return 0.
        kind, distance, wait, span = zone
        own_s, other_s = self.s, self.s+front['gap_m']
        own_v, other_v = self.v, front['speed_mps']
        envelope = self.prospective_corridor(goal)
        settled = abs(goal-self.offset)/1.5+.5
        decel = min(20., .65*self.c['brake_force_max_n']/self.c['effective_mass_kg'])*(1-.45*self.wet)
        decel = min(decel, max(.5, .70*self.brake_capacity()))
        other_decel = 30.*(1-.45*self.wet)
        drive = self.c['drive_force_n'][3 if self.c['ers_soc_mj']>.1 else 1]
        acceleration = clip((drive-self.c['drag_n']-self.c['rolling_resistance_n'])/self.c['effective_mass_kg'], .5, 10.)
        exit_accel = clip(max(3., front.get('acceleration',0.)), 3., 10.)
        for step in range(1, 61):
            t = step*.2
            limit = self.pace(own_s) if t < settled else self.at(envelope,own_s)
            new_own = own_v+clip(limit-own_v, -decel*.2, acceleration*.2)
            new_other = other_v+clip(self.pace(other_s)-other_v, -other_decel*.2, exit_accel*.2)
            gap = other_s-own_s
            if gap > 0:
                horizon = min(8., (gap+other_v*other_v/(2*other_decel)+7.2)/max(own_v,1.)+.45)
                conflict = False
                for j in range(int(horizon/.2)+2):
                    u = j*.2
                    moving = min(u, other_v/other_decel)
                    ours = own_s+own_v*u
                    theirs = other_s+other_v*moving-.5*other_decel*moving*moving
                    if abs(theirs-ours) <= 7.2+own_v*(.45+.2) and abs(self.lane(ours,goal,t+u)-self.rival_lane(front,theirs,t+u)) < 2.7:
                        conflict = True
                        break
                if conflict:
                    cap = max(0., math.sqrt((decel*.45)**2+2*decel*(max(0.,gap-7.2)+other_v*other_v/(2*other_decel)))-decel*.45)
                    new_own = min(new_own, cap)
            new_s = own_s+(own_v+new_own)*.1
            new_other_s = other_s+(other_v+new_other)*.1
            if new_s >= self.s+distance:
                fraction = clip((self.s+distance-own_s)/max(new_s-own_s,1e-9), 0., 1.)
                detector_v = own_v+(new_own-own_v)*fraction
                # vx projection from our public current body velocity, never
                # substitute a rival's speed or the hypot norm in the rule.
                detector_v *= clip(self.c['vx']/max(self.v,1.), 0., 1.)
                headway = (other_s+(new_other_s-other_s)*fraction-(self.s+distance))/max(detector_v,1.)
                if headway <= 0:
                    return 0.  # already past, no invented grant from that rival
                # A smooth forecast utility, NOT a replacement 1s eligibility
                # test. Small forecast errors cannot flip an all-or-nothing
                # tactical decision; actual availability is still engine-only.
                likelihood = clip((1.2-headway)/.2, 0., 1.)
                seconds = min(6., span/max(detector_v,20.))
                if kind == 0:
                    gain = .05*self.c['drag_n']/self.c['effective_mass_kg']
                else:
                    kph = detector_v*3.6
                    normal = float(np.interp(kph,self.mechanics['speed_kph'],self.mechanics['normal_w']))
                    boosted = float(np.interp(kph,self.mechanics['speed_kph'],self.mechanics['override_w']))
                    gain = max(0.,boosted-normal)*self.mechanics['wheel_efficiency']/max(20.,detector_v)/self.c['effective_mass_kg']
                    # No free energy: discount useful duration by current SOC.
                    seconds = min(seconds, self.c['ers_soc_mj']*1e6/max(boosted,1.))
                value = likelihood*.5*gain*seconds*seconds/(1+wait/max(detector_v,20.))
                self.diagnostic['detector_forecast'] = dict(kind=kind,headway_s=float(headway),distance_m=float(distance),goal=float(goal),future_progress_value_m=float(value))
                return value
            own_s, other_s, own_v, other_v = new_s, new_other_s, new_own, new_other
        return 0.

    def approach_conflict(self, front):
        """Propose independent braking paths before testing their net progress.

        Baseline-line reachability cannot veto a different GGV corridor.
        tactics() still compares every side with staying before commitment;
        this is a proposal, never official detection/activation eligibility.
        """
        if self.gg_active and self.pursuit_zone(front) is not None:
            self.diagnostic['detector_staging'] = True
            return True
        if (not self.gg_active or self.c['lap'] < 1 or front.get('merging')
                or not 90 < front['gap_m'] < 400):
            return False
        if self.catch_window(front) is not None:
            return True
        # Capturing a legal following gap before heavy braking is a distinct
        # opportunity from finishing a pass in six seconds. Waiting for the
        # latter leaves both cars on one line, then the unchanged emergency
        # stopping guard correctly brakes us out of the one-second window.
        # Prepare a separate corridor before that conflict; do not change the
        # stopping response, speed envelope, road limits or occupied-side rule.
        braking = min(self.pace(self.s+d) for d in (90.,180.,300.,400.)) < self.v-12.
        capture = (self.wet < .5 and self.defender is None
                   and not self.c['front_sliding'] and not self.c['rear_sliding']
                   and front['gap_m'] < 240. and braking)
        if capture:
            self.diagnostic['braking_capture_staging'] = True
        return capture

    def approach_loss(self, front, goal):
        """Rank progress using the same GGV path and unchanged stopping guard.

        Evaluate both passing sides before committing. A fixed offset need not
        fit every future road cross-section: lane(), steering and the physical
        corridor already project it inside the real walls. Future stopping
        overlap, rather than the intersection of unrelated widths, determines
        which side remains useful when the leader crosses a chicane. This is
        tactical ranking only; the real emergency guard is never bypassed.
        """
        own_s,other_s=self.s,self.s+front['gap_m']
        own_v,other_v=self.v,front['speed_mps']
        decel=min(20.,.65*self.c['brake_force_max_n']/self.c['effective_mass_kg'])*(1-.45*self.wet)
        if self.gg_active:
            decel=min(decel,max(.5,.70*self.brake_capacity()))
        other_decel=30.*(1-.45*self.wet)
        response=.45
        drive=self.c['drive_force_n'][3 if self.c['ers_soc_mj']>.1 else 1]
        own_accel=clip((drive-self.c['drag_n']-self.c['rolling_resistance_n'])/self.c['effective_mass_kg'],.5,10.)
        exit_accel=clip(max(3.,front.get('acceleration',0.)),3.,10.)
        loss=0.
        envelope=self.prospective_corridor(goal)
        settled=abs(goal-self.offset)/1.5+.5
        for step in range(1,11):
            t=step*.4
            own_limit=self.pace(own_s) if t<settled else self.at(envelope,own_s)
            new_own=own_v+clip(own_limit-own_v,-decel*.4,own_accel*.4)
            new_other=other_v+clip(self.pace(other_s)-other_v,-other_decel*.4,exit_accel*.4)
            own_s+=(own_v+new_own)*.2;other_s+=(other_v+new_other)*.2
            own_v,other_v=new_own,new_other
            gap=other_s-own_s
            if gap<=0:
                continue
            horizon=min(8.,(gap+other_v*other_v/(2*other_decel)+7.2)/max(own_v,1.)+response)
            stop_time=other_v/other_decel
            conflict=False
            for i in range(int(horizon/.2)+2):
                u=i*.2;moving=min(u,stop_time)
                ours=own_s+own_v*u
                theirs=other_s+other_v*moving-.5*other_decel*moving*moving
                if abs(theirs-ours)>7.2+own_v*(response+.2):
                    continue
                if abs(self.lane(ours,goal,t+u)-self.rival_lane(front,theirs,t+u))<2.7:
                    conflict=True
                    break
            if conflict:
                cap=max(0.,math.sqrt((decel*response)**2+2*decel*(max(0.,gap-7.2)+other_v*other_v/(2*other_decel)))-decel*response)
                loss+=max(0.,own_v-cap)*.4
        # Deficit relative to four seconds at current speed; a negative value
        # is predicted acceleration, not an eligibility/score threshold change.
        return loss+4.*self.v-(own_s-self.s)-self.qualification_value(front,goal)

    def path_value(self, goal, speed_cap=None):
        cap, risk = self.pace(self.s+self.v*.3) if speed_cap is None else speed_cap, 0.
        for t in (.35, .7, 1.3, 2.2, 3.5):
            own = self.lane(self.s+self.progress*t, goal, t)
            for r in self.rivals:
                if abs(r['gap_m']) > 130:
                    continue
                station = self.s+r['gap_m']+(self.progress+r['rate'])*t
                low, high = self.bounds(station)
                other = self.rival_lane(r, station, t)
                gap = r['gap_m']+r['rate']*t
                if abs(own-other) >= 2.6:
                    continue
                if r['gap_m'] > 0 and gap < 12+self.v*.15:
                    decel = min(6, .15*self.c['brake_force_max_n']/self.c['effective_mass_kg'])*(1-.45*self.wet)
                    progress_cap = max(0, self.progress+r['rate']+(r['gap_m']-12)/t+.5*decel*t)
                    cap = min(cap, progress_cap*self.v/max(self.progress, 1) if self.v > 2 else progress_cap)
                if abs(gap) < 5.45:
                    risk += 20/t
        return cap, cap/max(self.v, 20)-.025*abs(goal)-.025*abs(goal-self.offset)-risk, risk

    def tactics(self):
        o, c = self.o, self.c
        self.chasing = False
        if c['pit_state'] or o['phase'] != 'race':
            self.goal, self.target_car, self.mode = 0., None, 'PIT' if c['pit_state'] else 'LINE'
            self.defender = None
            return
        defended = next((r for r in self.rivals if r['seat'] == self.defender), None)
        if c['lap'] >= 1 and defended and defended['gap_m'] > 12:
            # The opponent has cleared our front axle. Remember the lost
            # defence, without a second defensive move across its occupied side.
            self.counter_car, self.counter_until = defended['seat'], o['tick']+600
            self.defender = None
        chase = min((r for r in self.rivals if 35 < r['gap_m'] < 280 and not r.get('merging')),
                    key=lambda r:r['gap_m'], default=None)
        if c['lap'] >= 1 and chase and not self.queue_context():
            advantage, window, _ = self.opportunity(chase)
            self.chasing = advantage > 1.5 and window != 'NONE'
            self.attack_window = window
        merges = [r for r in self.rivals if r.get('merging') and 0 < r['gap_m'] < 260]
        if self.gg_active and merges:
            exit_s = self.p['pit_exit_s']
            lo, hi = self.bounds(exit_s)
            base = self.at(self.safe_line, exit_s)
            # The public exit rejoins at the centre. Reserve an independent
            # on-track corridor before the kinematic transit ends; do not bet
            # on passing the merge point at one precise future instant.
            if lo <= -3.1 and all(not (-15 < r['gap_m'] < 15 and r['dn'] < -1.9) for r in self.rivals):
                self.goal, self.mode = -3.1-base, 'PASS_PIT_EXIT'
                self.target_car = min(merges, key=lambda r:r['gap_m'])['seat']
                return
        marked = next((r for r in self.rivals if r['seat'] == self.target_car), None)
        pursuit_lease = bool(marked and self.pursuit_car == marked['seat'] and self.s < self.pursuit_until
                             and self.mode == 'PASS' and self.defender is None and not marked.get('merging'))
        if self.target_car is not None and (not marked or marked['gap_m'] < -12 or (marked['gap_m'] > 120 and not self.approach_conflict(marked) and not self.committed_close(marked) and not pursuit_lease)):
            self.target_car, self.goal, self.mode = None, 0., 'CLEAR'
        if o['tick'] < self.next_tactic:
            return
        self.next_tactic = o['tick']+12
        if self.target_car is not None and marked:
            # Preserve an occupied/alongside commitment. Far behind, return to
            # the best line when the leader has escaped our six-second attack
            # horizon; waiting twelve seconds off-line only widens the gap.
            if (c['lap'] >= 1 and self.mode == 'PASS' and marked['gap_m'] > 60 and not self.committed_close(marked)
                    and self.catch_window(marked) is None and not self.approach_conflict(marked) and not pursuit_lease
                    and self.path_value(0.)[2] < 10):
                self.target_car,self.goal,self.mode=None,0.,'DRAFT'
                self.cooldown=o['tick']+120
                self.diagnostic['unreachable_attack_aborted']=True
                return
            if marked['gap_m'] < self.pass_gap-2:
                self.pass_gap, self.pass_progress_tick = marked['gap_m'], o['tick']
            stalled = o['tick']-self.pass_progress_tick > 12*60 and abs(marked['gap_m']) >= 12
            qualified = self.committed_close(marked) and (c['aid_available'] or c['override_available'] and c['ers_soc_mj']>.1)
            if stalled and not qualified:
                self.target_car, self.goal, self.mode = None, 0., 'RESET_ATTACK'
                self.cooldown = o['tick']+120
                return
            if self.path_value(self.goal)[2] < 10 or abs(marked['gap_m']) < 12:
                return
            self.target_car, self.cooldown = None, o['tick']+120
        self.goal, self.mode = 0., 'LINE'
        front = min((r for r in self.rivals if 0 < r['gap_m'] < 100 or self.approach_conflict(r)), key=lambda r: r['gap_m'], default=None)
        if front and o['tick'] >= self.cooldown:
            advantage, window, reachable = self.opportunity(front)
            staging = self.approach_conflict(front)
            self.diagnostic['approach_staging'] = staging
            possible = c['lap'] < 1 or staging or self.catch_window(front) is not None
            self.diagnostic['pass_reachable'] = possible
            if possible and (staging or reachable and advantage > .6) and not front.get('merging'):
                current = self.path_value(0)
                stations = [self.s+self.progress*t for t in (.7, 1.4, 2.2)]
                low = max(self.bounds(x)[0]-self.at(self.safe_line,x) for x in stations)
                high = min(self.bounds(x)[1]-self.at(self.safe_line,x) for x in stations)
                base = 0.
                options = []
                # Leave tracking tolerance outside the unchanged 2.7 m safety
                # guard. A nominal 2.85 m separation rarely clears it in a bend.
                separation = 3.35 if c['lap'] >= 1 else 2.85
                for offset in (front['offset']-separation, front['offset']+separation):
                    projected = (c['lap'] >= 1 and staging and self.wet < .5 and self.defender is None
                                 and not c['front_sliding'] and not c['rear_sliding'])
                    if not low-base <= offset <= high-base and not projected:
                        continue
                    cap, score, risk = self.path_value(offset)
                    if risk < 10:
                        loss=self.approach_loss(front,offset) if projected else 0.
                        score-=loss/max(self.v,20.)
                        options.append((score, cap, offset, risk, loss))
                if options:
                    score, cap, offset, risk, loss = max(options)
                    self.diagnostic['approach_progress_deficit_m'] = float(loss)
                    worthwhile=True
                    if projected:
                        stay=current[1]-self.approach_loss(front,0.)/max(self.v,20.)
                        worthwhile=score>stay
                        self.diagnostic.update(approach_stay_utility=float(stay),
                            approach_selected_utility=float(score),
                            staging_rejected_no_progress_gain=not worthwhile)
                    if worthwhile and (staging or cap > current[0]+.5 or current[2] > risk+1 or
                            (self.gg_active and window != 'NONE' and advantage > 1.5
                             and front['gap_m'] < 90 and score > current[1]-.2)):
                        # Start changing lane before the same-lane stopping cap
                        # forces a queue; waiting for a cap advantage is circular.
                        self.target_car, self.goal, self.mode = front['seat'], offset, 'PASS'
                        self.attack_window = window
                        self.pass_gap, self.pass_progress_tick = front['gap_m'], o['tick']
                        zone = self.pursuit_zone(front)
                        if zone is not None:
                            _, distance, wait, span = zone
                            self.pursuit_car = front['seat']
                            self.pursuit_until = self.s+distance+wait+min(span,400.)
                        return
            self.mode = 'DRAFT'
            if front['gap_m'] > 12+.18*self.progress and abs(front['offset']) < 2.5 and all(abs(row[1]) < .002 for row in o['preview']['rows'][:8]):
                self.goal = front['offset']
        previous = next((r for r in self.rivals if r['seat'] == self.defender), None)
        if not previous or previous['gap_m'] > 12 or previous['gap_m'] < -100:
            self.defender = None
        if self.defender is not None:
            self.goal, self.mode = self.defence_goal, 'DEFEND'
            return
        rear = max((r for r in self.rivals if -65 < r['gap_m'] < -12 and r['rate'] > .5), key=lambda r: r['gap_m'], default=None)
        if rear and not self.queue_context() and self.wet < .5 and o['tick']-self.pit_exit_tick > 300 and all(abs(r['gap_m']) > 12 for r in self.rivals) and all(abs(row[1]) < .002 for row in o['preview']['rows'][:6]):
            corner = next((row for row in o['preview']['rows'] if abs(row[1]) > .002), None)
            if corner:
                self.defender = rear['seat']
                self.goal = self.defence_goal = math.copysign(1.7, corner[1])
                self.mode = 'DEFEND'

    def follows(self, rival):
        """Use longitudinal position, then public seat, to break symmetry."""
        return rival['gap_m'] > .1 or (abs(rival['gap_m']) <= .1 and self.o['seat'] > rival['seat'])

    def traffic_path(self, goal):
        """Reserve occupied sides; only the trailing car yields at a bottleneck.

        A leader keeps its current lane until rear clearance. The follower keeps
        its side too, but sheds longitudinal speed. Thus no symmetric stop state
        exists, and yielding does not require cutting through a neighbouring car.
        """
        station = self.s+self.v*.4
        base = self.at(self.safe_line, station)
        yielding = False
        alongside = [r for r in self.rivals if abs(r['gap_m']) < 5.45 or
                     (abs(r['gap_m']) < 15 and abs(r['dn']) >= 1.9 and abs(r['gap_m']+r['rate']*.7) < 5.45)]
        low_offset, high_offset = -float('inf'), float('inf')
        for t in (0., .35, .7):
            station_t = self.s+self.progress*t
            reference = self.at(self.safe_line, station_t)
            low, high = self.bounds(station_t)
            for r in alongside:
                other_station = self.s+r['gap_m']+(self.progress+r['rate'])*t
                other_low, other_high = self.bounds(other_station)
                other = self.rival_lane(r, other_station, t)
                if r['dn'] > 0:
                    high = min(high, other-2.6)
                else:
                    low = max(low, other+2.6)
            low_offset, high_offset = max(low_offset, low-reference), min(high_offset, high-reference)
        low, high = low_offset+base, high_offset+base
        converging = [r for r in self.rivals if abs(r['gap_m']) < 8
                      and r['dn']*r.get('dn_rate', 0) < 0
                      and abs(r['dn'])+math.copysign(1., r['dn'])*r['dn_rate']*1.3 < 2.6]
        for r in converging:
            follower = self.follows(r)
            yielding |= follower
        if self.projects_pass() and not alongside and not converging:
            # Bounds apply to each actual waypoint, not to one offset shared
            # by unrelated road cross-sections. Preserve the planned side;
            # lane(), steering, GGV and the real stopping guard still bound it.
            return goal,False
        if low <= high:
            if yielding:
                self.mode = 'YIELD'
            return clip(base+goal, low, high)-base, yielding
        # Preserve the measured lane; do not manufacture an impossible lateral
        # interval and command both cars to stop. Public seat breaks exact ties.
        for r in alongside:
            follower = self.follows(r)
            yielding |= follower
        lo, hi = self.bounds(station)
        hold = clip(self.c['n'], lo, hi)-base
        self.mode = 'YIELD' if yielding else 'CLEAR_BOTTLENECK'
        return hold, yielding

    def queue_context(self):
        """Recognize a braking queue from public pace and nearby traffic."""
        preview = [self.pace(self.s+d) for d in (30, 90, 180, 300)]
        braking = min(preview[1:]) < max(self.v, preview[0])-12
        front = [r for r in self.rivals if 0 < r['gap_m'] < 90]
        return bool(front and (braking or len(front) >= 2 or
                    any(r['acceleration'] < -3 for r in front)))

    def stopping_conflict(self, rival, goal, other_decel, response):
        """Intersect swept footprints at the SAME future instant.

        Keep the leader's original worst-case emergency stop, response buffer
        and car width. Comparing lanes at unrelated longitudinal stations used
        to brake for a bend the leader had already cleared. Here the extra
        stopping constraint requires longitudinal AND lateral overlap. No
        private opponent grip, intent, battery or planned trajectory is used.
        """
        speed = max(self.progress, self.v, 1.)
        horizon = min(8., (rival['gap_m']+rival['speed_mps']**2/(2*other_decel)+7.2)/speed+response)
        stop_time = rival['speed_mps']/other_decel
        for t in np.arange(0., horizon+.100001, .1).tolist():
            moving = min(t, stop_time)
            other_s = self.s+rival['gap_m']+rival['speed_mps']*moving-.5*other_decel*moving*moving
            own_s = self.s+speed*t
            # The response distance also covers continuous crossing between
            # samples. Longitudinally unrelated lane convergence is irrelevant.
            if abs(other_s-own_s) > 7.2+speed*(response+.1):
                continue
            own_lane = self.lane(own_s, goal, t)
            other_lane = self.rival_lane(rival, other_s, t)
            if abs(own_lane-other_lane) < 2.7:
                self.diagnostic['swept_conflict_s'] = float(t)
                return True
        return False

    def trajectory_acceleration(self, original, target, speed_at, yielding, guard_braking):
        """Choose acceleration against arrival positions, not a reference's clock.

        Traffic can put a car below the reference speed. Reusing the reference
        deceleration then brakes it before it reaches its own braking point.
        Solve a constant-acceleration 0.4s rollout against the SAME physical
        envelope and all existing traffic/pit caps. Replan every callback;
        never raise a speed limit, grip budget, road margin or collision cap.
        """
        c = self.c
        if (self.o['phase'] != 'race' or c['lap'] < 1 or not self.gg_active
                or c['pit_state'] or yielding or guard_braking or self.mode == 'DEFEND'
                or c['front_sliding'] or c['rear_sliding']
                or abs(self.goal-self.offset) > .25
                or self.tracking_error() > .4
                or not (self.mode == 'PASS' or self.chasing)):
            return original
        def feasible(acceleration):
            for t in (.1,.2,.3,.4):
                stop = self.v/max(-acceleration,1e-9) if acceleration < 0 else t
                moving = min(t,stop)
                distance = max(0.,self.progress*moving+.5*acceleration*moving*moving)
                velocity = max(0.,self.v+acceleration*t)
                cap = min(target,speed_at(self.s+distance+velocity*self.ctrl['latency']))
                if velocity > cap:
                    return False
            return True
        low,high=-70.,40.
        if not feasible(low):
            return min(original,low)
        for _ in range(12):
            middle=(low+high)*.5
            if feasible(middle):low=middle
            else:high=middle
        self.diagnostic['arrival_control'] = True
        self.diagnostic['arrival_acceleration'] = float(low)
        return low

    def straight_attack(self, acceleration, target, speed_at, yielding, guard_braking):
        """Recover speed on a stable straight, never release corner braking.

        The old feed-forward was a reference trajectory's deceleration even
        when traffic had left the car well below that trajectory. A local
        receding speed target can catch the train before a detection line.
        Restrict this to a measured stable lane, a clear stopping-length
        straight and an actual public rival. All collision/merge/pit caps and
        the original corner feed-forward remain authoritative.
        """
        c = self.c
        if (self.o['phase'] != 'race' or c['lap'] < 1 or c['pit_state']
                or yielding or guard_braking or self.mode == 'DEFEND'
                or c['front_sliding'] or c['rear_sliding']
                or abs(self.goal-self.offset) > .25
                or self.tracking_error() > .4
                or target <= self.v+.3 or not any(8 < r['gap_m'] < 400 and not r.get('merging') for r in self.rivals)):
            return acceleration
        horizon = max(80., self.v*self.v/(2*max(4., self.brake_capacity() or 4.))+self.v*.6)
        stations = self.s+np.arange(0.,horizon+10.,10.)
        # Only genuine straight-line recovery. v14/v15's unrestricted capture
        # failed in bends, so it is deliberately not inherited here.
        if any(abs(self.at(self.reference_k,x)) > .001 for x in stations):
            return acceleration
        ceiling = min(speed_at(x) for x in stations[:max(2,int(self.v*.6/10)+2)])
        recovery = clip((min(target,ceiling)-self.v)/.6, -70., 40.)
        if recovery > acceleration:
            self.diagnostic['straight_recovery'] = True
            return recovery
        return acceleration

    def longitudinal_guard(self, goal):
        """Return a response-time speed cap and necessary braking response.

        Independent lateral occupancy releases this constraint only when both
        the current lane and its stopping approach leave another car's width.
        Later-lap predictions cover the actual stopping time and legal visible range;
        a 0.7-second-only check missed converging queues at high closing speed.
        A leader never yields to a rear car, preventing a symmetric stop.
        """
        if self.o['phase'] != 'race' or self.c['pit_state']:
            return float('inf'), 0.
        queued = self.queue_context()
        response = .45 if queued else .25
        own_decel = min(20., .65*self.c['brake_force_max_n']/self.c['effective_mass_kg'])*(1-.45*self.wet)
        if self.gg_active:
            own_decel = min(own_decel, max(.5, .70*self.brake_capacity()))
        other_decel = 30.*(1-.45*self.wet)
        cap, braking = float('inf'), 0.
        for r in self.rivals:
            visible_range = 400 if self.c['lap'] >= 1 else 130
            if not .1 < r['gap_m'] < visible_range:
                continue
            if self.c['lap'] < 1:
                # Preserve the proven first-lap queue response exactly.
                separations = [abs(r['dn'])]
                for t in (.35, .7):
                    own = self.lane(self.s+self.progress*t, goal, t)
                    other = self.rival_lane(r, self.s+r['gap_m']+r['speed_mps']*t, t)
                    separations.append(abs(own-other))
                conflict = min(separations) < 2.7
            else:
                conflict = self.stopping_conflict(r, goal, other_decel, response)
            if not conflict:
                continue
            available = max(0., r['gap_m']-7.2)
            safe_speed = max(0., math.sqrt((own_decel*response)**2+2*own_decel*(available+r['speed_mps']**2/(2*other_decel)))-own_decel*response)
            cap = min(cap, safe_speed)
            if self.v > safe_speed+.5:
                braking = max(braking, own_decel)
        self.diagnostic.update(queue=queued, response_s=response, guard_decel=float(braking))
        return cap, braking

    def pit_approach(self, compound):
        """Separate arrival times before the uncontrollable pit transit.

        Intent of another driver is hidden. Conservatively space any visible
        car ahead near the entrance; a longitudinal tie goes to the lower seat.
        This can cost a short delay if that car stays on the road.
        """
        c, o = self.c, self.o
        distance = (self.p['pit_entry_s']-self.s) % self.length
        if compound is None or c['pit_state'] or distance > 400:
            self.entry_front = None
            return float('inf')
        candidates = [r for r in self.rivals if self.follows(r) and -.1 <= r['gap_m'] < 180]
        front = min(candidates, key=lambda r:r['gap_m'], default=None)
        if front is None:
            return float('inf')
        self.entry_front = front['seat']
        # A fixed transit preserves entry headways. 1.4 seconds comfortably
        # exceeds a car length at the public pit speed, including a tick.
        headway = 1.4
        front_time = (distance-front['gap_m'])/max(front['speed_mps'], 1.)
        target_time = front_time+headway
        cap = distance/max(target_time, .1)
        self.diagnostic.update(pit_entry_front=front['seat'], pit_arrival_gap_s=headway)
        return cap

    def pit(self):
        c, o = self.c, self.o
        if o['phase'] != 'race' or c['pit_state']:
            return False, None
        wet = o['weather']['condition'] == 'wet'
        schedule = self.ctrl.get('wet_stops', self.ctrl.get('stops', [])) if wet else self.ctrl.get('stops', [])
        stop = schedule[c['pit_stops']] if c['pit_stops'] < len(schedule) else None
        worn = max(c['tyre_wear']) > .8
        # A short race does not justify an extra healthy-tyre stop just because
        # the demonstration schedule lists it. Satisfy the compound rule once;
        # a further stop needs observed wear or a genuine weather transition.
        if c['pit_stops'] >= 1 and len(self.compounds) >= 2 and max(c['tyre_wear']) < .55:
            stop = None
        # The observed wet-race waiver persists through damp/dry transitions.
        # Retire the old dry schedule; fresh wet-weather and wear needs below
        # remain actionable. No compound means no phantom pre-entry spacing.
        if self.wet_race and not worn:
            stop = None
        if stop and c['lap'] == 0 and c['compound'] == 'soft' and not worn:
            stop = None
        required = not self.wet_race and len(self.compounds) < 2 and c['lap'] >= 1
        if wet and c['compound'] != 'soft' and c['lap'] < o['laps_total']-1:
            stop = dict(lap=c['lap'], compound='soft', request_ahead_m=180)
        if stop is None and required:
            stop = dict(lap=c['lap'], compound='medium' if c['compound'] == 'soft' else 'soft')
        if stop is None and worn and c['lap'] < o['laps_total']-1:
            stop = dict(lap=c['lap'], compound='hard')
        if not stop or c['lap'] < stop['lap'] or c['lap'] >= o['laps_total']-1:
            return False, None
        compound = 'soft' if wet else stop['compound']
        if not wet and compound == c['compound']:
            compound = 'medium' if c['compound'] == 'soft' else 'soft'
        distance = (self.p['pit_entry_s']-self.s) % self.length
        return self.s > 0 and distance <= stop.get('request_ahead_m', 150), compound

    def steering(self):
        p, c, v = self.p, self.c, self.v
        ahead = max(self.ctrl['ld_min']+3*self.wet*(1+self.thermal), self.ctrl['ld_time']*v*(1+.2*self.wet))
        index, travelled = int((self.s % self.length)/self.ds), 0.
        while travelled < ahead:
            travelled += p['line_ds'][index]
            index = (index+1) % self.count
        station = index*self.ds
        low, high = self.bounds(station)
        if self.o['phase'] == 'race':
            seconds = ahead/max(self.progress, 5)
            for rival in self.rivals:
                gap = rival['gap_m']+rival['rate']*seconds
                if abs(rival['gap_m']) < 6.5 or (abs(gap) < 6.5 and abs(rival['dn']) >= 1.9):
                    other = self.rival_lane(rival, self.s+rival['gap_m']+(self.progress+rival['rate'])*seconds, seconds)
                    if rival['dn'] > 0:
                        high = min(high, other-2.6)
                    else:
                        low = max(low, other+2.6)
        n = clip(self.safe_line[index]+self.offset, low, high) if low <= high else clip(c['n'], *self.bounds(station))
        x = self.x[index]+(n-self.line[index])*self.nx[index]
        y = self.y[index]+(n-self.line[index])*self.ny[index]
        dx, dy = x-c['x'], y-c['y']
        forward = dx*math.cos(c['yaw'])+dy*math.sin(c['yaw'])
        side = -dx*math.sin(c['yaw'])+dy*math.cos(c['yaw'])
        curvature = 2*side/max(1, forward*forward+side*side)
        u = clip((v-10)/5, 0, len(p['k_us'])-1.001)
        j, f = int(u), u-int(u)
        table = self.tables[c['compound']]
        stiffness = []
        for key, temp in (('front_stiffness', sum(c['tyre_temp_c'][:2])/2), ('rear_stiffness', sum(c['tyre_temp_c'][2:])/2)):
            i, t = self.bracket(table['temperatures'], temp)
            values = table[key]
            stiffness.append((values[i, j]*(1-f)+values[i, j+1]*f)*(1-t)+(values[i+1, j]*(1-f)+values[i+1, j+1]*f)*t)
        cold = p['cold_caps'][c['compound']]
        understeer = (p['dry_mass']+c['fuel_kg'])/p['wheelbase']*(cold['b']/stiffness[0]-cold['a']/stiffness[1])
        gain = max(p['wheelbase']*.7, p['wheelbase']+understeer*v*v)
        value = clip(gain*(curvature+self.ctrl['yaw_gain']*(v*curvature-c['yaw_rate'])/max(v, 8))/p['steer_max'], -1, 1)
        if self.wet:
            rate = 1.6*math.sqrt(1-.45*self.wet)*(1-.25*self.wet*self.thermal)*self.dt/p['steer_max']
            value = clip(value, c['steer']/p['steer_max']-rate, c['steer']/p['steer_max']+rate)
        return clip(value, -1, 1)

    def plan_energy_windows(self):
        """Allocate deployment opportunities from this car's public lap table.

        Use contiguous aero-straight spans, split at the finish line. Short
        transitions are not deployment destinations. Duration weights use the
        existing speed plan; no detection receipt or rival hidden state enters.
        """
        windows = []
        start = None
        for i in range(self.count+1):
            active = i < self.count and self.p['xmode'][i] > .5
            if active and start is None:
                start = i
            if not active and start is not None:
                if (i-start)*self.ds >= 200:
                    seconds = sum(self.ds/max(20., self.p['v_plan'][j]) for j in range(start, i))
                    windows.append((start*self.ds, min(self.length, i*self.ds), seconds))
                start = None
        return tuple(windows)

    def deployment_approach_safe(self):
        """Avoid spending reserved energy on an already predicted brake queue.

        This is an additional deployment veto, not a replacement for the
        longitudinal guard. Project only visible rivals, with the same side
        clearance, response time and braking assumptions as that guard.
        """
        decel = min(20., .65*self.c['brake_force_max_n']/self.c['effective_mass_kg'])*(1-.45*self.wet)
        if self.gg_active:
            decel = min(decel, max(.5, .70*self.brake_capacity()))
        other_decel = 30.*(1-.45*self.wet)
        response = .45 if self.queue_context() else .25
        for r in self.rivals:
            if not 0 < r['gap_m'] < 130:
                continue
            for t in (.35, .7, 1.3, 2.2):
                own_s = self.s+self.progress*t
                other_s = self.s+r['gap_m']+r['speed_mps']*t
                own_lane = self.lane(own_s, self.goal, t)
                other_lane = self.rival_lane(r, other_s, t)
                if abs(own_lane-other_lane) >= 2.7:
                    continue
                own_v = min(self.v, self.pace(own_s))
                other_v = min(r['speed_mps'], self.pace(other_s))
                gap = other_s-own_s
                safe = max(0., math.sqrt((decel*response)**2+2*decel*(max(0., gap-7.2)
                               +other_v*other_v/(2*other_decel)))-decel*response)
                if gap < 7.2 or own_v > safe+.5:
                    return False
        return True

    def energy_budget_active(self, yielding, guard_braking):
        """Whether the straight schedule replaces the legacy scalar reserve."""
        c = self.c
        return bool(self.energy_windows and self.o['phase'] == 'race' and c['lap'] >= 1
                    and not c['pit_state'] and self.mode != 'DEFEND' and not yielding
                    and guard_braking == 0 and not c['aid_available'] and not c['override_available']
                    and not c['front_sliding'] and not c['rear_sliding']
                    and c['fuel_kg'] > 0 and c['pu_temp_c'] <= 115)

    def budget_deployment(self, ers, yielding, guard_braking):
        """Ring-fence remaining-lap energy; PASS/chasing cannot spend it early.

        Freeze a floor at each straight's entry, weighted by the durations of
        this and later straights. Recomputing it every callback would drain it
        geometrically. Public SOC at the next entry includes actual harvesting.
        Only electric deployment is reduced; speed, steering, brake and all
        original safety guards remain authoritative.
        """
        c = self.c
        if (self.mode == 'DEFEND' and ers >= 2 and c['ers_soc_mj'] < .8
                and not c['override_available'] and not c['aid_available']):
            self.diagnostic['defence_energy_reserve'] = True
            return 1
        if not self.energy_budget_active(yielding, guard_braking):
            return ers
        station = self.s % self.length
        zone = next((i for i, (start, end, _) in enumerate(self.energy_windows)
                     if start <= station < end), None)
        if self.energy_lap != c['lap']:
            self.energy_lap, self.energy_zone = c['lap'], None
            self.energy_floor = .04
        if zone is not None and zone != self.energy_zone:
            remaining = sum(w[2] for w in self.energy_windows[zone:])
            fraction = self.energy_windows[zone][2]/remaining
            self.energy_floor = .04+max(0., c['ers_soc_mj']-.04)*(1-fraction)
            self.energy_zone = zone
        if zone is None:
            self.energy_zone = None
        self.diagnostic.update(energy_zone=zone, energy_floor_mj=float(self.energy_floor))
        if ers < 2:
            return ers
        reason = None
        if zone is None:
            reason = 'STORE_FOR_STRAIGHT'
        elif c['ers_soc_mj'] <= self.energy_floor:
            reason = 'FUTURE_STRAIGHT_RESERVE'
        elif not self.deployment_approach_safe():
            reason = 'PREDICTED_BRAKE_QUEUE'
        if reason:
            self.diagnostic['energy_veto'] = reason
            return 1
        self.diagnostic['energy_release'] = True
        return ers

    def allocate_power(self, ers, force, yielding, guard_braking):
        """Preserve electric energy when ICE can meet the same planned force.

        This changes the force source, never the velocity or safety plan. The
        published ICE force is a steady-state estimate; turbo lag and extra
        fuel use still require a real paired run before claiming a benefit.
        """
        c = self.c
        eligible = (self.o['phase'] == 'race' and c['lap'] >= 1 and not c['pit_state']
                    and self.mode != 'DEFEND' and not yielding and guard_braking == 0
                    and not c['aid_available'] and not c['override_available']
                    and not c['front_sliding'] and not c['rear_sliding']
                    and c['fuel_kg'] > 0 and c['pu_temp_c'] <= 115)
        if (eligible and ers >= 2 and .05*c['drive_force_n'][ers] < force
                <= c['drive_force_n'][1]):
            self.diagnostic.update(deployment_substitution=True, planned_drive_force=float(force),
                                   neutral_force_capacity=float(c['drive_force_n'][1]))
            return 1
        return ers

    def act(self, observation):
        self.diagnostic = {}
        self.cache_scalar_queries = True
        self.perceive(observation)
        self.tactics()
        c, o, s, v = self.c, self.o, self.s, self.v
        goal = self.goal
        if o['phase'] == 'race' and s < 330 and self.target_car is None and self.mode != 'DEFEND':
            fraction = clip((s-30)/300, 0, 1)
            goal = (1-fraction*fraction*(3-2*fraction))*(self.grid_n-self.at(self.safe_line, s))
        goal, yielding = self.traffic_path(goal)
        self.offset += clip(goal-self.offset, -1.5*self.dt, 1.5*self.dt)
        steer = self.steering()
        envelope = self.corridor_envelope(goal, yielding)
        envelope = self.power_envelope(envelope, goal, yielding)
        speed_at = lambda station: self.at(envelope, station)
        station = s+v*self.ctrl['latency']
        now, following = speed_at(station), speed_at(station+self.ds)
        target = min(now, speed_at(s+v*self.ctrl['preview']))
        feedforward = (following*following-now*now)/(2*self.ds)
        # The car clearing a bottleneck must retain longitudinal authority. Rear
        # predicted overlaps are not reasons for the leading car to brake.
        if self.mode != 'CLEAR_BOTTLENECK':
            target = min(target, self.path_value(goal, speed_at(self.s+self.v*.3))[0])
        self.diagnostic['traffic_cap'] = float(target)
        for r in self.rivals:
            if not 0 < r['gap_m'] < 65 or abs(r['dn']) >= 2.6:
                continue
            escaping = abs(self.lane(s+self.progress*.7, goal, .7)-(c['n']+r['dn'])) >= 2.6
            gap = 8+.16*self.progress/(1-.45*self.wet)
            # A rear yield is deliberate; a leader ignores the following car.
            if yielding or not escaping or r['gap_m'] < gap or r['acceleration'] < -2:
                follow = max(0, v+(r['rate']+.8*(r['gap_m']-gap))*(v/max(self.progress, 1) if v > 2 else 1))
                if follow < target:
                    target, feedforward = follow, min(0, r['acceleration'])
        if yielding:
            target = min(target, max(0, v-3))
            feedforward = min(0., feedforward)
        self.diagnostic['follow_cap'] = float(target)
        safety_cap, guard_braking = self.longitudinal_guard(goal)
        target = min(target, safety_cap)
        self.diagnostic['stopping_cap'] = float(target)
        merge_cap = target
        for r in self.rivals:
            if r.get('merging') and 0 < r['gap_m'] < 220:
                other_at_exit = self.s+r['gap_m']+r['speed_mps']*r['merge_time']
                own_at_exit = self.s+self.progress*r['merge_time']
                safely_clear_lane = abs(c['n']) >= 2.8 and abs(self.lane(self.p['pit_exit_s'], goal, r['merge_time'])) >= 2.8
                guard = not safely_clear_lane if self.gg_active else own_at_exit < other_at_exit+8
                if guard:
                    decel = min(20., .65*c['brake_force_max_n']/c['effective_mass_kg'])*(1-.45*self.wet)
                    if self.gg_active:
                        decel = max(.5, self.brake_capacity())
                    safe = math.sqrt(max(0., r['speed_mps']**2+2*decel*max(0., r['gap_m']-8-.2*v)))
                    merge_cap = min(merge_cap, safe)
        target = min(target, merge_cap)
        self.diagnostic['merge_cap'] = float(merge_cap)
        error = self.tracking_error()
        target *= 1-min(.25, max(0, error-.8)*.05)
        index = int((s % self.length)/self.ds)
        target *= math.sqrt(clip(1-self.offset*self.reference_k[index], .5, 1))
        if o['phase'] == 'race' and feedforward < 0 and v < now:
            feedforward *= v/max(now, 1)
        request, compound = self.pit()
        entry_cap = self.pit_approach(compound)
        target = min(target, entry_cap)
        self.diagnostic['pit_entry_cap'] = float(target)
        acceleration = clip(feedforward+self.ctrl['kv']*(target-v), -70, 40)
        acceleration = self.trajectory_acceleration(acceleration, target, speed_at, yielding, guard_braking)
        if guard_braking > 0:
            acceleration = min(acceleration, -guard_braking)
        contest = (self.mode in ('PASS', 'PASS_PIT_EXIT', 'DEFEND') and target > v+.3) or any(0 < r['gap_m'] < 65 and r['speed_mps'] < target for r in self.rivals)
        contest = contest or self.chasing
        finishing = s > o['laps_total']*self.length-500
        reserve = 1.6 if o['phase'] == 'race' and c['lap'] >= 1 else .8
        if acceleration < -.5:
            ers = 0
        elif c['ers_soc_mj'] < .04 or c['pu_temp_c'] > 115 or (c['ers_soc_mj'] < reserve and not finishing and not contest
                and not self.energy_budget_active(yielding, guard_braking)):
            ers = 1
        elif target > v+.3:
            ers = 3 if contest or o['phase'] == 'qualifying' or finishing else 2
        else:
            ers = 1
        inertial_mass = self.p['dry_mass']+c['fuel_kg'] if self.gg_active and acceleration < 0 else c['effective_mass_kg']
        force = acceleration*inertial_mass+c['drag_n']+c['rolling_resistance_n']
        ers = self.budget_deployment(ers, yielding, guard_braking)
        ers = self.allocate_power(ers, force, yielding, guard_braking)
        throttle = clip(force/max(1, c['drive_force_n'][ers]), 0, 1) if force > 0 else 0.
        brake = clip(-force/c['brake_force_max_n']*self.ctrl['brake_gain'], 0, 1) if force < 0 else 0.
        if self.gg_active and brake > 0:
            available = float(self.capability_brake(v, abs(c.get('g_lat', 0))*9.81, c.get('aero_mode', 0)))
            brake = min(brake, .9*available/c['brake_force_max_n'])
        if c['rear_sliding']:
            throttle *= .6
        if c['front_sliding']:
            brake *= .75
        if (yielding or target < .5) and v < 1:
            throttle, brake = 0., max(.1, brake)
        covered = s-self.fuel_station
        if o['phase'] == 'race' and covered > self.length*.25:
            need = max(0, self.fuel_start-c['fuel_kg'])/covered*max(0, o['laps_total']*self.length-s)*1.03
            if need > c['fuel_kg']:
                throttle *= clip(c['fuel_kg']/need, .25, 1)
        if c['pu_temp_c'] > 115:
            throttle *= clip((135-c['pu_temp_c'])/20, .15, 1)
        self.diagnostic.update(target=float(target), free=float(now), feedforward=float(feedforward),
                               error=float(error), yielding=bool(yielding), physical_capability=bool(self.gg_active),
                               chasing=bool(self.chasing), attack_window=self.attack_window,
                               aid_available=bool(c['aid_available']), override_available=bool(c['override_available']))
        aero = int(self.at(self.p['xmode'], s+v*.35) > .5)
        override = o['phase'] == 'race' and c['override_available'] and throttle > .5 and brake < .05 and c['ers_soc_mj'] > .1
        aid = o['phase'] == 'race' and c['aid_available'] and c['aid_zone'] is not None and aero == 1 and brake < .05
        self.cache_scalar_queries = False
        return dict(throttle=float(throttle), brake=float(brake), steer=float(steer), ers_mode=ers,
                    aero_mode=aero, override=bool(override), aid=bool(aid), pit_request=bool(request), pit_compound=compound)


_driver = None


def load_plan(weights_dir):
    """Restore frozen public tables from numeric NPZ, without pickle or fallback.

    Args:
        weights_dir: The runtime's read-only materialized submission directory.

    Returns:
        Original ordered dictionaries, lists and Python scalar types.
    """
    with np.load(Path(weights_dir) / 'plan.npz', allow_pickle=False) as archive:
        # Access every array while the file is open; no lazy handle survives init.
        arrays = {key: archive[key] for key in archive.files}
    if set(arrays) != {'float', 'int', 'bool', 'structure'}:
        raise ValueError('Frozen planning tensor keys mismatch')
    for key, dtype in {'float': '<f8', 'int': '<i8', 'bool': '|b1',
                       'structure': '|u1'}.items():
        if arrays[key].dtype.str != dtype or arrays[key].ndim != 1:
            raise ValueError('Frozen planning tensor dtype/shape mismatch: ' + key)
    structure = json.loads(arrays['structure'].tobytes().decode('utf-8'))
    if structure['format'] != 'formula-public-plan-v1':
        raise ValueError('Unknown frozen planning format')

    def restore(node):
        kind, value = node[:2]
        if kind == 'dict':
            return {key: restore(child) for key, child in value}
        if kind == 'list':
            return [restore(child) for child in value]
        if kind == 'literal':
            return value
        if kind == 'array':
            start, shape = node[2:]
            count = math.prod(shape)
            return arrays[value][start:start + count].reshape(shape).tolist()
        raise ValueError('Unknown frozen planning node')

    return restore(structure['root'])


def load_mechanics(weights_dir, length):
    """Read immutable public rule/track tensors; malformed weights fail init."""
    with np.load(Path(weights_dir)/'mechanics.npz', allow_pickle=False) as archive:
        data = {key:archive[key] for key in archive.files}
    expected = {'version','length','zones','speed_kph','normal_w','override_w','wheel_efficiency'}
    if set(data) != expected or data['version'].shape != (1,) or data['version'][0] != 1:
        raise ValueError('Public mechanics format mismatch')
    if any(value.dtype.kind not in 'iuf' or not np.isfinite(value).all() for value in data.values()):
        raise ValueError('Public mechanics must be finite numeric tensors')
    if data['length'].shape != (1,) or abs(float(data['length'][0])-length)>1e-8:
        raise ValueError('Public mechanics track binding mismatch')
    zones=data['zones'];grid=data['speed_kph']
    if (zones.ndim!=2 or zones.shape[1]!=4 or len(zones)>32
            or not np.isin(zones[:,0],[0.,1.]).all()
            or not ((zones[:,1:]>=0)&(zones[:,1:]<length)).all()
            or grid.ndim!=1 or len(grid)<2 or not (np.diff(grid)>0).all()
            or any(data[k].shape!=grid.shape or (data[k]<0).any() for k in ('normal_w','override_w'))
            or data['wheel_efficiency'].shape!=(1,) or not 0<float(data['wheel_efficiency'][0])<=1.):
        raise ValueError('Public mechanics tensor shape/range mismatch')
    data['wheel_efficiency']=float(data['wheel_efficiency'][0])
    return data


def init(context):
    """Load this seat's frozen NPZ through the public weights_dir contract."""
    global _driver
    _driver = None
    weights = Path(context['weights_dir']) / 'weights'
    data = load_plan(weights)
    mechanics = load_mechanics(weights, data['plan']['L'])
    _driver = Driver(data['plan'], data['control'], mechanics)
    from preview_driver import init as preview_init
    preview_init(context)


def act(observation):
    """Return all nine legal action fields from a frozen public observation."""
    if _driver is None:
        raise RuntimeError('init(context) must be called before act(observation)')
    # The frozen line, pit stations and speed tables describe Parkland.
    # Track lengths in observations are rounded to the nearest millimetre.
    if abs(observation['track_length_m'] - _driver.length) > 0.5:
        from preview_driver import act as preview_act
        return preview_act(observation)
    return _driver.act(observation)
