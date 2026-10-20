#!/usr/bin/env python3
"""Compile the patched production touch methods and pointer state against an input oracle.

Run with the pinned scrcpy checkout; no Android attachment or network is used.
The builder also calls test_source() on its exported, patched source tree.
"""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def method(source,name):
    start=source.index('    private boolean '+name+'(')
    brace=source.index('{',start);depth=1;end=brace+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]


MOTION='''package android.view;
public class MotionEvent {
 public static final int ACTION_DOWN=0,ACTION_UP=1,ACTION_MOVE=2,ACTION_CANCEL=3,ACTION_POINTER_DOWN=5,
 ACTION_POINTER_UP=6,ACTION_HOVER_MOVE=7,ACTION_BUTTON_PRESS=11,ACTION_BUTTON_RELEASE=12,
 ACTION_POINTER_INDEX_SHIFT=8,BUTTON_PRIMARY=1,TOOL_TYPE_MOUSE=3,TOOL_TYPE_FINGER=1;
 public static class PointerProperties {public int id,toolType;}
 public static class PointerCoords {public float x,y,pressure;}
 public int action,count,source,mode;public float x;public boolean recycled;
 public static MotionEvent obtain(long down,long now,int action,int count,PointerProperties[] p,PointerCoords[] c,
   int meta,int buttons,float xp,float yp,int device,int edge,int source,int flags) {
  MotionEvent e=new MotionEvent();e.action=action;e.count=count;e.source=source;e.x=c[0].x;return e;
 }
 public void recycle(){recycled=true;}
}'''

HEADER='''package com.genymobile.scrcpy.control;
import android.view.MotionEvent;
import com.genymobile.scrcpy.device.Point;
import java.util.ArrayList;
public class ResetTest {
 static final int DEFAULT_DEVICE_ID=-1;static final long POINTER_ID_MOUSE=-1;
 long lastTouchDown;int lastTouchDisplayId,lastTouchSource=InputDevice.SOURCE_TOUCHSCREEN;
 boolean touchResetFailed;final PointersState pointersState=new PointersState();
 final MotionEvent.PointerProperties[] pointerProperties=new MotionEvent.PointerProperties[PointersState.MAX_POINTERS];
 final MotionEvent.PointerCoords[] pointerCoords=new MotionEvent.PointerCoords[PointersState.MAX_POINTERS];
 ResetTest(){for(int i=0;i<PointersState.MAX_POINTERS;i++){pointerProperties[i]=new MotionEvent.PointerProperties();pointerCoords[i]=new MotionEvent.PointerCoords();}}
 static class Pair<A,B>{A first;B second;Pair(A a,B b){first=a;second=b;}}
 static class Position{boolean valid;Position(boolean v){valid=v;}}
 Pair<Point,Integer> getEventPointAndDisplayId(Position p){return p.valid?new Pair<>(new Point(50,70),0):null;}
 static class InputDevice{static final int SOURCE_TOUCHSCREEN=0x1002,SOURCE_MOUSE=0x2002;}
 static class Build{static class VERSION{static final int SDK_INT=35;}}
 static class AndroidVersions{static final int API_23_ANDROID_6_0=23;}
 static class SystemClock{static long time;static long uptimeMillis(){return ++time;}}
 static class InputManager{static final int INJECT_INPUT_EVENT_MODE_WAIT_FOR_RESULT=2;
   static boolean setActionButton(MotionEvent e,int b){return true;}}
 static class Ln{static void i(String s){}static void w(String s){}}
 static class Device{
  static final int INJECT_MODE_ASYNC=0;static boolean cancelAccepted,failDown;
  static final ArrayList<MotionEvent> events=new ArrayList<>();
  static boolean injectEvent(MotionEvent e,int display,int mode){
   e.mode=mode;events.add(e);
   if(e.action==MotionEvent.ACTION_CANCEL)return cancelAccepted;
   if(e.action==MotionEvent.ACTION_DOWN&&failDown){failDown=false;return false;}
   return true;
  }
 }
 static void check(boolean value){if(!value)throw new AssertionError();}
 static MotionEvent last(){return Device.events.get(Device.events.size()-1);}
 boolean send(int action,long id){return injectTouch(action,id,new Position(true),1f,0,0);}
'''

CASES='''
 public static void main(String[] args){
  ResetTest c=new ResetTest();
  check(c.send(0,7));check(c.send(0,8));check(c.pointersState.size()==2);
  check(!c.send(3,7));check(c.touchResetFailed);check(last().count==2&&last().action==3&&last().mode==2&&last().recycled);
  int before=Device.events.size();check(!c.send(2,7));check(!c.send(1,8));check(Device.events.size()==before);
  check(c.send(0,9));check(last().action==0&&last().count==1&&last().x==50);
  check(!c.touchResetFailed&&c.pointersState.size()==1);check(c.send(1,9));check(c.pointersState.size()==0);

  check(c.send(0,7));check(!c.send(3,7));Device.failDown=true;
  check(!c.send(0,9));check(c.touchResetFailed);before=Device.events.size();
  check(!c.send(2,9));check(!c.send(1,9));check(Device.events.size()==before);
  check(c.send(0,10));check(last().count==1&&last().action==0&&!c.touchResetFailed);

  check(!c.send(3,10));before=Device.events.size();
  check(!c.injectTouch(0,4,new Position(false),1f,0,0));check(c.touchResetFailed&&Device.events.size()==before);
  check(!c.send(3,4));check(!c.send(2,4));check(c.send(0,4));
  check(last().count==1&&last().action==0);
  Device.cancelAccepted=true;check(c.send(3,4));
  check(c.pointersState.size()==0&&!c.touchResetFailed);check(c.send(0,2));
  check(last().action==0&&last().count==1);check(c.cancelTouches());check(c.pointersState.size()==0);
  System.out.println("Patched scrcpy cancellation/recreation recovery: PASS");
 }
}'''


def test_source(root):
    source=root/'server/src/main/java/com/genymobile/scrcpy'
    controller=(source/'control/Controller.java').read_text()
    with tempfile.TemporaryDirectory(prefix='anvil-scrcpy-reset-') as directory:
        out=Path(directory)
        motion=out/'android/view/MotionEvent.java';motion.parent.mkdir(parents=True);motion.write_text(MOTION)
        files=[motion]
        for name in ('control/Pointer.java','control/PointersState.java','device/Point.java'):
            destination=out/'com/genymobile/scrcpy'/name;destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes((source/name).read_bytes());files.append(destination)
        test=out/'com/genymobile/scrcpy/control/ResetTest.java'
        test.write_text(HEADER+'\n'.join(method(controller,name) for name in ('cancelTouches','injectTouch','injectTouchInternal'))+CASES)
        subprocess.run(['javac','-d',str(out),*map(str,files),str(test)],check=True)
        subprocess.run(['java','-cp',str(out),'com.genymobile.scrcpy.control.ResetTest'],check=True)


if __name__=='__main__':
    checkout=Path(sys.argv[1]).resolve();repo=Path(__file__).resolve().parents[2]
    revision=subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'],text=True).strip()
    if revision!='f01231dff8294fe2c99045a4f9a14b233a71bb86':raise RuntimeError('Unexpected scrcpy revision')
    with tempfile.TemporaryDirectory(prefix='anvil-scrcpy-export-') as directory:
        archive=subprocess.check_output(['git','-C',str(checkout),'archive','--format=zip','HEAD','server'])
        zipfile.ZipFile(io.BytesIO(archive)).extractall(directory)
        subprocess.run(['patch','-p1','-i',str(repo/'native/input/scrcpy-3.3.1-cancel.patch')],cwd=directory,check=True)
        test_source(Path(directory))
