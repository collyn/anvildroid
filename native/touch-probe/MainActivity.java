package org.anvildroid.touchprobe;

import android.app.Activity;
import android.os.Bundle;
import android.os.SystemClock;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.util.Log;
import android.util.SparseArray;
import android.view.MotionEvent;
import android.view.View;
import java.util.ArrayDeque;
import java.util.Locale;

/** No permissions, injection or IPC. Observe only events delivered to this app. */
public final class MainActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(new ProbeView());
    }

    private final class ProbeView extends View {
        final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        final SparseArray<float[]> points = new SparseArray<>();
        final ArrayDeque<String> lines = new ArrayDeque<>();
        long sequence;
        final float textSize;

        ProbeView() {
            super(MainActivity.this);
            textSize = 14 * getResources().getDisplayMetrics().scaledDensity;
            setFocusableInTouchMode(true);
            log("CREATE uptimeMs=" + SystemClock.uptimeMillis());
        }

        void log(String message) {
            String line = (++sequence) + " " + message;
            Log.i("AnvilTouchProbe", line);
            if (lines.size() == 40) lines.removeFirst();
            lines.addLast(line);
            invalidate();
        }

        @Override protected void onSizeChanged(int w, int h, int oldw, int oldh) {
            super.onSizeChanged(w, h, oldw, oldh);
            log("SIZE contentPx=" + w + "x" + h + " density=" + getResources().getDisplayMetrics().density);
        }

        @Override public void onWindowFocusChanged(boolean focused) {
            super.onWindowFocusChanged(focused);
            // Do NOT synthesize CANCEL or erase held points here: doing so would
            // hide a stuck gesture in the injector under test.
            log("FOCUS=" + focused + " active=" + points.size());
        }

        @Override public boolean onTouchEvent(MotionEvent event) {
            long received = SystemClock.uptimeMillis();
            int action = event.getActionMasked();
            if (action == MotionEvent.ACTION_DOWN && points.size() != 0)
                log("WARNING new DOWN with active=" + points.size());
            if (action == MotionEvent.ACTION_DOWN) points.clear();
            StringBuilder row = new StringBuilder(MotionEvent.actionToString(event.getAction()));
            row.append(" eventMs=").append(event.getEventTime()).append(" receivedMs=").append(received)
                .append(" ageMs=").append(received-event.getEventTime())
                .append(" source=0x").append(Integer.toHexString(event.getSource()))
                .append(" device=").append(event.getDeviceId())
                .append(" history=").append(event.getHistorySize());
            for (int i=0; i<event.getPointerCount(); ++i) {
                int id = event.getPointerId(i);
                float x=event.getX(i), y=event.getY(i);
                points.put(id, new float[]{x,y});
                row.append(String.format(Locale.ROOT," id=%d(%.1f,%.1f)",id,x,y));
            }
            if (action == MotionEvent.ACTION_POINTER_UP)
                points.remove(event.getPointerId(event.getActionIndex()));
            else if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL)
                points.clear();
            row.append(" activeAfter=").append(points.size());
            log(row.toString());
            return true;
        }

        @Override protected void onDraw(Canvas canvas) {
            canvas.drawColor(Color.rgb(18,24,30));
            paint.setColor(Color.rgb(65,80,94)); paint.setStrokeWidth(1);
            for(int i=1;i<4;++i) {
                canvas.drawLine(getWidth()*i/4f,0,getWidth()*i/4f,getHeight(),paint);
                canvas.drawLine(0,getHeight()*i/4f,getWidth(),getHeight()*i/4f,paint);
            }
            paint.setColor(Color.WHITE); paint.setTextSize(textSize);
            float y=textSize*1.5f;
            canvas.drawText("Touch Probe | content " + getWidth()+"x"+getHeight()+" | active="+points.size(),12,y,paint);
            y+=textSize*1.5f;
            canvas.drawText("ageMs = Android event age, NOT host-to-Android latency",12,y,paint);
            for(String line:lines) {
                y+=textSize*1.3f;
                if(y>getHeight()-textSize) break;
                canvas.drawText(line,12,y,paint);
            }
            for(int i=0;i<points.size();++i) {
                float[] p=points.valueAt(i);
                paint.setColor(Color.rgb(76,220,160)); canvas.drawCircle(p[0],p[1],18,paint);
                paint.setColor(Color.BLACK); canvas.drawText(""+points.keyAt(i),p[0]-5,p[1]+5,paint);
            }
        }
    }
}
