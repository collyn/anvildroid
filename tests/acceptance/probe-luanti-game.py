#!/usr/bin/env python3
"""Opt-in real Minetest Game acceptance in a disposable live-android fixture.

Uses the official, unmodified Luanti APK and Minetest Game release. A world-local
fixture mod initializes the scene once on join, then observes actual player
position, look, wield and controls. It does not implement or synthesize input.
All measured input goes through private XTest -> production bridge -> Android.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time


def module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


isolation=module('probe-editor-isolation')
probe=isolation.probe
require=probe.require
PACKAGE='net.minetest.minetest'
USER='/data/media/0/Android/data/'+PACKAGE+'/files/Minetest'
WORLD=USER+'/worlds/Acceptance'
PINS={
    'apk':'d0e8bea52462d7e743d411e79c9eaa6b55f382f88ab1644d6ef727b320fae148',
    'game':'33a3bb43b08497a0bdb2f49f140a2829e582d5c16c0ad52be1595c803f706912',
}
CONFIG='''touch_controls = true
touch_gui = true
touch_interaction_style = buttons_crosshair
touch_layout = {"version":1,"layout":{"dig":{"position_x":0.85,"position_y":0.6,"offset_x":0,"offset_y":0},"place":{"position_x":0.85,"position_y":0.4,"offset_x":0,"offset_y":0},"overflow":{"position_x":0.95,"position_y":0.1,"offset_x":0,"offset_y":0}}}
fixed_virtual_joystick = false
touchscreen_threshold = 10
touchscreen_sensitivity = 0.12
touch_long_tap_delay = 250
keymap_forward = SYSTEM_SCANCODE_82
keymap_backward = SYSTEM_SCANCODE_81
keymap_left = SYSTEM_SCANCODE_80
keymap_right = SYSTEM_SCANCODE_79
keymap_jump = SYSTEM_SCANCODE_106
creative_mode = true
static_spawnpoint = 0,9,0
enable_damage = false
enable_server = false
name = singleplayer
menu_last_game = minetest
mainmenu_last_selected_world = 1
mainmenu_last_selected_TP = 1
address =
screen_w = 1100
screen_h = 700
fps_max = 30
wanted_fps = 20
viewing_range = 30
enable_shaders = false
enable_dynamic_shadows = false
enable_clouds = false
sound_volume = 0
server_announce = false
serverlist_url =
contentdb_url =
autosave_screensize = false
hud_scaling = 1
'''
OBSERVER='''local path = core.get_worldpath()
local epoch = tostring(core.get_us_time())
local seq, elapsed, ready = 0, 0, false
local digs, places = 0, 0
core.register_on_dignode(function() digs = digs + 1 end)
core.register_on_placenode(function() places = places + 1 end)
core.register_on_joinplayer(function(player)
    ready = false
    os.remove(path..'/acceptance-state.json')
    core.after(2, function()
        if not player:is_player() then return end
        core.emerge_area({x=-16,y=0,z=-16},{x=16,y=31,z=16},function(_,_,left)
        if left ~= 0 or not player:is_player() then return end
        local air, floor = {}, {}
        for x=-12,12 do for z=-12,12 do
            floor[#floor+1] = {x=x,y=8,z=z}
            for y=9,24 do air[#air+1] = {x=x,y=y,z=z} end
        end end
        core.bulk_set_node(air,{name='air'})
        core.bulk_set_node(floor,{name='default:dirt_with_grass'})
        player:set_pos({x=0,y=9,z=0})
        player:set_look_horizontal(0)
        player:set_look_vertical(0.03)
        local inv = player:get_inventory()
        inv:set_stack('main',1,'default:pick_steel')
        inv:set_stack('main',2,'default:stone 99')
        core.set_node({x=0,y=10,z=3},{name='default:dirt'})
        ready = true
        end)
    end)
end)
core.register_globalstep(function(dt)
    elapsed = elapsed + dt
    if elapsed < 0.1 or not ready then return end
    elapsed = 0
    for _,p in ipairs(core.get_connected_players()) do
        seq = seq + 1
        local state = {epoch=epoch, seq=seq, pos=p:get_pos(), yaw=p:get_look_horizontal(),
            pitch=p:get_look_vertical(), controls=p:get_player_control(),
            wield=p:get_wield_index(), digs=digs, places=places,
            target=core.get_node({x=0,y=10,z=3}).name,
            window=core.get_player_window_information(p:get_player_name())}
        core.safe_file_write(path..'/acceptance-state.json',core.write_json(state))
    end
end)
'''


def pinned(path,kind):
    data=path.read_bytes()
    require(hashlib.sha256(data).hexdigest()==PINS[kind],'Unexpected official '+kind+' bytes')
    return data


def distance(a,b):
    return math.hypot(a['pos']['x']-b['pos']['x'],a['pos']['z']-b['pos']['z'])


def angle(a,b):
    d=a['yaw']-b['yaw']
    return abs(math.atan2(math.sin(d),math.cos(d)))


def neutral(s):
    c=s['controls']
    return abs(c['movement_x'])<.01 and abs(c['movement_y'])<.01 and not any(c[k] for k in ('dig','place','jump'))


def assets(game):
    """Repack only regular, safe game files; never follow archive links."""
    output=io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(game)) as source,tarfile.open(fileobj=output,mode='w') as dest:
        for member in source:
            parts=Path(member.name).parts
            require(parts and parts[0]=='minetest_game-5.8.0' and '..' not in parts,
                    'Unexpected game archive path')
            if not member.isfile():
                require(member.isdir(),'Links or special game files are not allowed');continue
            target='games/minetest/'+str(Path(*parts[1:]))
            data=source.extractfile(member).read()
            info=tarfile.TarInfo(target);info.size=len(data);info.mode=0o644
            dest.addfile(info,io.BytesIO(data))
        files={'minetest.conf':CONFIG,'worlds/Acceptance/world.mt':
            'gameid = minetest\nbackend = sqlite3\nplayer_backend = sqlite3\nauth_backend = sqlite3\ncreative_mode = true\nenable_damage = false\n',
            'worlds/Acceptance/map_meta.txt':'mg_name = flat\nseed = 451\nmgflat_ground_level = 8\nmg_flags = light\n[end_of_params]\n',
            'worlds/Acceptance/worldmods/anvil_observe/mod.conf':'name = anvil_observe\ndepends = default\n',
            'worlds/Acceptance/worldmods/anvil_observe/init.lua':OBSERVER}
        for name,contents in files.items():
            data=contents.encode();info=tarfile.TarInfo(name);info.mode=0o644;info.size=len(data)
            dest.addfile(info,io.BytesIO(data))
    return output.getvalue()


class Session(isolation.Endpoint):
    def raw(self,command,**kwargs):
        self.guard()
        return subprocess.run(command,check=True,capture_output=True,timeout=30,**kwargs).stdout

    def prepare(self,apk,game):
        pinned(apk,'apk');payload=assets(pinned(game,'game'))
        fd=os.open(apk,os.O_RDONLY)
        try:
            value=self.backend.status(self.m['id'],'install',
                dict(op='install',generation=self.m['generation']),apk_fd=fd)
            require(value.get('generation')==self.m['generation'] and
                    value.get('app_result',{}).get('success'),'Game install failed')
        finally:os.close(fd)
        self.request('app_action',action='launch',package=PACKAGE)
        deadline=time.monotonic()+30
        while True:
            try:
                files=self.run(self.guest+['/system/bin/ls',USER]).splitlines()
                if all(name in files for name in ('builtin','client','textures','debug.txt')):break
            except subprocess.CalledProcessError:pass
            require(time.monotonic()<deadline,'Official engine assets did not unpack');time.sleep(.3)
        self.request('app_action',action='force_stop',package=PACKAGE)
        # This path is created only by this probe in the authenticated disposable
        # guest. Remove saved camera/player state so a rerun starts the same scene.
        self.run(self.guest+['/system/bin/rm','-rf',WORLD])
        uid=re.search(r'\buserId=(\d+)',self.run(self.guest+['/system/bin/dumpsys','package',PACKAGE]))
        require(uid is not None and int(uid[1])>=10000,'Unexpected game Android UID')
        self.raw(self.guest+['/system/bin/tar','-xf','-','-C',USER],input=payload)
        self.run(self.guest+['/system/bin/chown','-R',uid[1]+':'+uid[1],USER])
        self.write_profile()
        self.request('app_action',action='launch',package=PACKAGE)

    def write_profile(self):
        rows=[probe.v3_point(kind=7,source=2),
            probe.v3_point(key=17,kind=1,nx=10000,ny=50000,radius=12000,ramp_ms=100),
            probe.v3_point(key=18,kind=2,nx=10000,ny=50000,ex=10000,ey=35000,duration=200,end_mode=2),
            probe.v3_point(key=273,source=1,kind=8,nx=45000,ny=30000,
                look_width=18000,look_height=18000,sensitivity_x=80,sensitivity_y=80),
            probe.v3_point(key=57,nx=55705,ny=39321,end_mode=2)]
        profile=f'ANVIL_KEYMAP 3 {self.m["id"]} {PACKAGE} {len(rows)}\n'+''.join(' '.join(map(str,r))+'\n' for r in rows)
        directory='/data/waydroid_tmp/anvildroid/keymaps';path=directory+'/'+PACKAGE
        self.run(self.guest+['/system/bin/mkdir','-p',directory])
        self.run(self.guest+['/system/bin/touch',path])
        self.run(self.guest+['/system/bin/chown','1000:1000',directory,path])
        self.run(self.guest+['/system/bin/chmod','0700',directory])
        self.run(self.guest+['/system/bin/chmod','0600',path])
        self.run(self.guest[:-1]+['--uid','1000','--gid','1000','--','/system/bin/sh','-c','cat > '+path],input=profile.encode())
        (self.output/'game-profile.txt').write_text(profile)

    def locate(self):
        deadline=time.monotonic()+15
        while True:
            try:return self.locate_once()
            except (subprocess.CalledProcessError,RuntimeError):
                if time.monotonic()>=deadline:raise
                time.sleep(.2)

    def locate_once(self):
        windows=self.run(self.x+['search','--onlyvisible','--class',
            'weston' if self.nested else r'\.'+re.escape(PACKAGE)+'$']).splitlines()
        require(len(windows)==1,'Ambiguous game window')
        self.window=windows[0];self.offset=(0,0)
        if self.nested:
            require(self.window==self.transport['x11_window'],'Compositor output changed')
            scene=self.scene();marker=' of waydroid.anvildroid.'+self.m['id']+'.'+PACKAGE+','
            sections=[part for part in scene.split('\n\tView ') if 'role xdg_toplevel' in part and marker in part]
            require(len(sections)==1 and '[view is not mapped!]' not in sections[0],'Ambiguous mapped game surface')
            bounds=re.search(r'position: \((-?\d+), (-?\d+)\) -> \((-?\d+), (-?\d+)\)',sections[0])
            require(bounds is not None,'Game surface bounds missing')
            x,y,right,bottom=map(int,bounds.groups());require(0<=x<right<=1600 and 0<=y<bottom<=1000,'Game outside private output')
            self.offset=(x,y);self.width,self.height=right-x,bottom-y
            self.frame=(0,0)
        else:
            values=dict(row.split('=',1) for row in self.run(self.x+['getwindowgeometry','--shell',self.window]).splitlines() if '=' in row)
            self.width,self.height=int(values['WIDTH'])-10,int(values['HEIGHT'])-38
            self.frame=(5,33)
        self.run(self.x+['windowfocus','--sync',self.window])
        logs=self.run(self.guest+['/system/bin/logcat','-d','-s','AnvilKeymapOverlay:I','*:S'])
        geometry=re.findall(r'attached '+re.escape(PACKAGE)+r' origin=(\d+),(\d+) size=(\d+)x(\d+)',logs)
        geometry=[tuple(map(int,g)) for g in geometry]
        require(geometry,'Fitted game marker geometry missing')
        ox,oy,w,h=geometry[-1]
        require(0<=ox and 0<=oy and w>0 and h>0 and
                abs(2*ox+w-self.width)<=2 and abs(2*oy+h-self.height)<=2,
                'Latest fitted geometry does not match current content')
        self.frame=(self.frame[0]+ox,self.frame[1]+oy);self.width,self.height=w,h

    def normalize(self):
        """Fit only this authenticated fixture's game inside its private output."""
        if not self.nested:
            windows=self.run(self.x+['search','--onlyvisible','--class',r'\.'+re.escape(PACKAGE)+'$']).splitlines()
            require(len(windows)==1,'Ambiguous game window')
            self.run(self.x+['windowsize','--sync',windows[0],'1400','900'])
            return
        self.window=self.transport['x11_window'];self.offset=(0,0)
        marker=' of waydroid.anvildroid.'+self.m['id']+'.'+PACKAGE+','
        def bounds():
            sections=[part for part in self.scene().split('\n\tView ') if 'role xdg_toplevel' in part and marker in part]
            require(len(sections)==1 and '[view is not mapped!]' not in sections[0],'Ambiguous mapped game')
            match=re.search(r'position: \((-?\d+), (-?\d+)\) -> \((-?\d+), (-?\d+)\)',sections[0])
            require(match is not None,'Game surface bounds missing')
            return tuple(map(int,match.groups()))
        x,y,right,bottom=bounds()
        if 0<=x<right<=1600 and 0<=y<bottom<=1000:return
        require(x<=700<right and y<=400<bottom,'Cannot safely reach game CSD')
        self.run(self.x+['windowfocus','--sync',self.window]+self.move(700,400)+
                 ['keydown','Super_L','mousedown','1','mousemove_relative','--','0','140',
                  'mouseup','1','keyup','Super_L'])
        time.sleep(.3);x,y,right,bottom=bounds()
        require(0<=x<1500 and 33<=y<=800,'Cannot safely reach game header')
        self.run(self.x+self.move(x+min(700,(right-x)//2),y-16)+['click','--repeat','2','--delay','150','1'])
        time.sleep(.5)

    def point(self,nx,ny):
        return self.move(self.frame[0]+round(nx*(self.width-1)),self.frame[1]+round(ny*(self.height-1)))

    def snapshot(self,label):
        data=self.raw(self.guest+['/system/bin/screencap','-p'])
        require(data.startswith(b'\x89PNG\r\n\x1a\n'),'Incomplete Android screenshot')
        (self.output/(label+'.png')).write_bytes(data)

    def configure_capture(self):
        """Change the existing Look through the real editor; one Look per map."""
        logs=lambda:self.run(self.guest+['/system/bin/logcat','-d','-s','AnvilKeymapOverlay:I','*:S']).splitlines()
        before=logs();self.run(self.x+['key','F2']);deadline=time.monotonic()+5
        while not any('request '+PACKAGE+' ' in row and ' accepted=1 ' in row for row in logs()[len(before):]):
            require(time.monotonic()<deadline,'Game editor did not attach');time.sleep(.1)
        time.sleep(.4)
        self.locate()
        def click(x,y):
            self.run(self.x+self.move(self.frame[0]+x,self.frame[1]+y)+['click','1']);time.sleep(.3)
        self.run(self.x+self.point(45000/65535,30000/65535)+['click','1'])
        time.sleep(.3)
        w,h=self.width,self.height;buttons=probe.editor_targets(w,h)
        click(*buttons['bind_key']);self.run(self.x+['key','l']);time.sleep(.15)
        compact=w<600 or h<420;cols=2 if not compact and h>=540 else 4
        rh=32 if cols==2 else 24;px,py,pw=(12,12,w-88) if compact else (w-236,304,208)
        def prop(i):return px+(i%cols)*pw//cols+(pw//cols-2)//2,py+(i//cols)*rh+(rh-2)//2
        for _ in range(3):click(*prop(2))  # Binding -> Region -> Sensitivity -> Look
        click(*prop(5))  # Hold -> Capture
        click(*buttons['save'])
        path='/data/waydroid_tmp/anvildroid/keymaps/'+PACKAGE
        deadline=time.monotonic()+5
        while True:
            saved=self.run(self.guest+['/system/bin/cat',path])
            rows=[tuple(map(int,row.split())) for row in saved.splitlines()[1:]]
            look=[row for row in rows if row[4]==8]
            if len(look)==1 and look[0][0]==38 and look[0][9]==0 and look[0][18]==1:break
            require(time.monotonic()<deadline,'Game editor did not save capture binding');time.sleep(.1)
        (self.output/'capture-profile.txt').write_text(saved)
        while not any('editor removed package='+PACKAGE+' ' in row for row in logs()[len(before):]):
            require(time.monotonic()<deadline,'Game editor did not close after Save');time.sleep(.1)
        time.sleep(.3)

    def game_state(self):
        return json.loads(self.run(self.guest+['/system/bin/cat',WORLD+'/acceptance-state.json']))

    def wait(self,predicate,label,after=None,timeout=12):
        deadline=time.monotonic()+timeout
        while True:
            try:
                value=self.game_state()
                require(not hasattr(self,'epoch') or value['epoch']==self.epoch,'Game epoch changed during a scenario')
                if (after is None or value['seq']>after) and predicate(value):
                    (self.output/(label+'.json')).write_text(json.dumps(value,indent=2));return value
            except (subprocess.CalledProcessError,json.JSONDecodeError):pass
            require(time.monotonic()<deadline,'Game state acknowledgment timed out: '+label)
            time.sleep(.1)

    def release(self):
        self.run(self.x+['keyup','w','a','s','d','e','l','space','Super_L','mouseup','1','mouseup','3'])

    def cancel(self):
        # A capture toggle survives key-up. Blur the private display's client
        # as well, including on a failed assertion, to cancel that lock/contact.
        tree=self.run(self.input_prefix+self.env+['xwininfo','-root'])
        root=re.search(r'Window id: (0x[0-9a-fA-F]+)',tree)
        require(root is not None,'Private display root missing')
        self.run(self.x+['windowfocus',root[1]])
        self.release()

    def settled_camera(self,label,after):
        """Drain already queued game frames before checking post-release motion."""
        previous=self.wait(neutral,label+'-start',after=after)
        stable=0;deadline=time.monotonic()+12
        while stable<5:
            value=self.wait(neutral,label+'-sample',after=previous['seq'],timeout=2)
            stable=stable+1 if angle(value,previous)<.001 else 0
            previous=value
            require(time.monotonic()<deadline,'Game camera did not settle: '+label)
        (self.output/(label+'.json')).write_text(json.dumps(previous,indent=2))
        return previous

    def sync_nested_pointer(self):
        # Weston-on-X11 consumes relative deltas while locked. After unlock its
        # logical position can differ from XTest's absolute outer cursor by the
        # accumulated motion. Clamp both private 1600x1000 coordinate spaces at
        # opposite corners, then return to content without clicking the shell.
        if self.nested:
            self.run(self.x+['mousemove','--window',self.window,'1599','999','sleep','.2',
                             'mousemove','--window',self.window,'0','0','sleep','.2']+
                     self.point(.4,.3)+['sleep','.2'])

    def play(self):
        try:old=self.game_state().get('epoch')
        except (subprocess.CalledProcessError,json.JSONDecodeError):old=None
        self.run(self.x+self.point(.685,.665)+['click','1'])
        value=self.wait(lambda v:v.get('epoch') is not None and v['epoch']!=old and neutral(v) and
                        abs(v['pos']['x'])<.05 and abs(v['pos']['z'])<.05 and abs(v['pos']['y']-8.5)<.05,
                        'world-ready',timeout=35)
        self.epoch=value['epoch']
        return self.wait(neutral,'world-settled',after=value['seq']+10)

    def exercise(self):
        results=[]
        def passed(name,**data):
            results.append(dict(scenario=name,passed=True,**data))
            print(name,'PASS',flush=True)
        self.release()
        before=self.game_state()
        self.epoch=before['epoch']
        baseline=self.wait(neutral,'baseline',after=before['seq'])
        self.snapshot('baseline')
        self.run(self.x+['keydown','space'])
        dug=self.wait(lambda v:v['digs']>baseline['digs'] and v['target']=='air',
                      'mapped-dig-node',after=baseline['seq'])
        self.run(self.x+['keyup','space'])
        baseline=self.wait(neutral,'dig-released',after=dug['seq'])
        passed('mapped-button-digs-real-node',digs=dug['digs'],target=dug['target'])
        window=baseline['window'];w,h=window['size']['x'],window['size']['y']
        require(window['touch_controls'] and w>0 and h>0,'Game did not acknowledge touch mode/render target')
        # Upstream HUD formula with the fixture's hud_scaling=1 and eight slots.
        scale=window['real_hud_scaling'];size=math.floor(48*scale+.5)
        slot=size+2*(size//12)
        self.run(self.x+self.point((w/2-2.5*slot)/(w-1),(h-4*scale-slot/2)/(h-1))+['click','1'])
        selected=self.wait(lambda v:v['wield']==2,'mouse-tap-hotbar',after=baseline['seq'])
        passed('mouse-as-finger-tap-hotbar',wield=selected['wield'])
        # Negative control: native W is deliberately rebound to an arrow key
        # inside this game fixture. Touch camera ignores ordinary mouse motion.
        self.run(self.x+['key','F3'])
        self.run(self.x+self.point(.65,.5)+['mousedown','1']+self.point(.75,.5)+
                 ['sleep','.3','mouseup','1','keydown','w','sleep','.6','keyup','w'])
        raw=self.wait(neutral,'unmapped-native-input',after=baseline['seq']+3)
        require(distance(raw,baseline)<.1 and angle(raw,baseline)<.02,'Native input bypassed the intended touch-only control test')
        passed('native-mouse-camera-and-unmapped-w',distance=distance(raw,baseline),yaw_delta=angle(raw,baseline))
        self.run(self.x+['key','F3'])

        self.run(self.x+self.point(.65,.5)+['mousedown','1']+self.point(.75,.5)+['sleep','.2','mouseup','1'])
        looked=self.wait(lambda v:angle(v,raw)>.1,'mouse-as-finger-camera',after=raw['seq'])
        passed('mouse-as-finger-camera',yaw_delta=angle(looked,raw))
        self.snapshot('mouse-camera')

        self.run(self.x+['keydown','w'])
        moving=self.wait(lambda v:v['controls']['movement_y']>.2 and distance(v,looked)>.3,
                         'joystick-held',after=looked['seq'])
        self.run(self.x+['keyup','w'])
        stopped=self.wait(neutral,'joystick-released',after=moving['seq'])
        settled=self.wait(neutral,'joystick-settled',after=stopped['seq']+4)
        require(distance(settled,stopped)<.15,'Player kept moving after joystick release')
        passed('joystick-drag-and-hold',distance=distance(moving,looked),release_drift=distance(settled,stopped))

        self.run(self.x+['keydown','e'])
        swiped=self.wait(lambda v:v['controls']['movement_y']>.2 and distance(v,settled)>.3,
                         'swipe-held',after=settled['seq'])
        self.run(self.x+['keyup','e'])
        released=self.wait(neutral,'swipe-released',after=swiped['seq'])
        passed('swipe-endpoint-hold',distance=distance(swiped,settled))

        self.run(self.x+self.point(.65,.5)+['mousedown','3']+self.point(.75,.5))
        camera=self.wait(lambda v:angle(v,released)>.05,'look-held',after=released['seq'])
        self.run(self.x+['mouseup','3'])
        self.wait(neutral,'look-released',after=camera['seq'])
        passed('look-binding-camera',yaw_delta=angle(camera,released))

        self.run(self.x+['keydown','w']+self.point(.65,.5)+['mousedown','3']+
                 self.point(.73,.5)+['keydown','space'])
        combined=self.wait(lambda v:v['controls']['movement_y']>.2 and v['controls']['dig'] and
                           angle(v,camera)>.05,'movement-look-dig',after=camera['seq'])
        self.release()
        self.wait(neutral,'combined-released',after=combined['seq'])
        passed('simultaneous-joystick-look-dig',movement=combined['controls']['movement_y'],dig=True)

        self.run(self.x+['keydown','w'])
        active=self.wait(lambda v:v['controls']['movement_y']>.2,'blur-held')
        root=self.run(self.x+['getwindowfocus']).strip()
        tree=self.run(self.input_prefix+self.env+['xwininfo','-root'])
        xid=re.search(r'Window id: (0x[0-9a-fA-F]+)',tree)
        require(xid is not None,'Private root missing')
        self.run(self.x+['windowfocus',xid[1]])
        self.wait(neutral,'blur-cancelled',after=active['seq'])
        self.run(self.x+['windowfocus','--sync',root])
        if self.nested:self.run(self.x+self.point(.65,.3)+['click','1'])
        time.sleep(.3)
        self.run(self.x+['keyup','w'])
        passed('blur-cancels-game-movement')
        fresh=self.wait(neutral,'refocused',after=active['seq'])
        self.run(self.x+['keydown','w'])
        resumed=self.wait(lambda v:v['controls']['movement_y']>.2,'refocus-fresh-input',after=fresh['seq'])
        self.run(self.x+['keyup','w'])
        self.wait(neutral,'refocus-fresh-released',after=resumed['seq'])
        passed('fresh-input-after-blur')

        self.configure_capture()
        # Continuous real relative motion must keep changing the game camera,
        # including across authored look-region edges, until Escape releases it.
        # The nested X11 transport cursor is distinct from the locked Wayland
        # pointer. Leave enough outer-display space for all 600 relative pixels.
        self.run(self.x+self.point(.4,.3)+['click','1','sleep','.3','key','l','sleep','.4'])
        start=time.monotonic();last=self.game_state();deltas=[]
        for batch in range(6):
            command=[]
            for _ in range(50):command+=['mousemove_relative','--','2','0','sleep','.1']
            self.run(self.x+command)
            value=self.wait(lambda v:angle(v,last)>.05,'capture-batch-'+str(batch),after=last['seq'])
            deltas.append(angle(value,last));last=value
        elapsed=time.monotonic()-start
        require(elapsed>=30,'Sustained capture interval too short')
        self.run(self.x+['key','Escape'])
        cancelled=self.settled_camera('capture-escaped',after=last['seq']+3)
        self.sync_nested_pointer()
        def pointer():
            fields=dict(row.split('=',1) for row in self.run(self.x+['getmouselocation','--shell']).splitlines() if '=' in row)
            return int(fields['X']),int(fields['Y'])
        pointer_before=pointer()
        self.run(self.x+['mousemove_relative','--','10','0','sleep','.5'])
        pointer_after=pointer()
        require(pointer_before!=pointer_after,'Post-Escape motion hit the outer display edge')
        quiet=self.settled_camera('capture-escape-quiet',after=cancelled['seq']+2)
        require(angle(quiet,cancelled)<.02,'Escape left the game camera captured')
        fresh=self.point(.4,.3)+['sleep','.2','key','l','sleep','.4']
        for _ in range(6):fresh+=['mousemove_relative','--','10','0','sleep','.1']
        self.run(self.x+fresh)
        recovered=self.wait(lambda v:angle(v,quiet)>.05,'capture-fresh-toggle',after=quiet['seq'])
        self.run(self.x+['key','Escape'])
        self.wait(neutral,'capture-final-neutral',after=recovered['seq']+3)
        passed('sustained-fps-capture-escape-recovery',seconds=elapsed,batch_yaw_deltas=deltas,
               released_pointer_before=pointer_before,released_pointer_after=pointer_after)
        self.snapshot('final')
        summary=dict(runtime=self.m['id'],generation=self.m['generation'],
            package_sha256=self.m['package_sha256'],game='Minetest Game 5.8.0',engine='Luanti 5.17.0 x86_64',
            input_backend='wayland' if self.nested else 'native-x11',inputs=PINS,
            epoch=self.epoch,game_window=baseline['window'],
            fixture_mod='scene initialization and player state observation; no synthetic input',results=results)
        (self.output/'summary.json').write_text(json.dumps(summary,indent=2));return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('metadata',type=Path);p.add_argument('pid',type=int);p.add_argument('output',type=Path)
    p.add_argument('--prepare',action='store_true');p.add_argument('--apk',type=Path);p.add_argument('--game',type=Path)
    p.add_argument('--normalize-window',action='store_true',help='Fit the scoped game inside the private display before testing')
    p.add_argument('--enter',action='store_true');p.add_argument('--run',action='store_true')
    a=p.parse_args();require(os.geteuid()==0,'Root required')
    a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=True)
    s=Session(a.metadata,a.pid,a.output)
    if a.prepare:
        require(a.apk and a.game,'Preparation requires both pinned official inputs');s.prepare(a.apk,a.game)
    time.sleep(.5)
    if a.normalize_window:s.normalize()
    s.locate();s.sync_nested_pointer();s.snapshot('prepared' if a.prepare else 'current')
    print(json.dumps(dict(worker=s.pid,width=s.width,height=s.height,window=s.window,offset=s.offset)))
    try:
        if a.enter:s.play()
        if a.run:print(json.dumps(s.exercise(),indent=2))
    except Exception as error:
        (a.output/'failure.json').write_text(json.dumps(dict(runtime=s.m['id'],
            generation=s.m['generation'],error=str(error)),indent=2))
        raise
    finally:
        if a.enter or a.run:s.cancel()


if __name__=='__main__':main()
