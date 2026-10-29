package o00OoOO0.OooOOOO.sensorlab;

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;

public class TestActivity extends Activity {
    static volatile TestActivity current;
    TextView view;
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        current = this;
        view = new TextView(this);
        view.setTextSize(19);
        view.setPadding(28, 40, 28, 28);
        view.setText("Local Step Lab\n\nIndependent local helper.\nNo internet permission.\n\nStart through its own instrumentation.\nNo production application is instrumented.");
        setContentView(view);
    }
    static void show(final String text) {
        final TestActivity activity = current;
        if (activity != null) activity.runOnUiThread(new Runnable() {
            public void run() { activity.view.setText(text); }
        });
    }
    @Override protected void onDestroy() {
        if (current == this) current = null;
        super.onDestroy();
    }
}
