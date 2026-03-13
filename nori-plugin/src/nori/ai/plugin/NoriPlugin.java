package nori.ai.plugin;

import org.eclipse.jface.preference.IPreferenceStore;
import org.eclipse.ui.plugin.AbstractUIPlugin;
import org.osgi.framework.BundleContext;

/**
 * Nori AI 플러그인 Activator
 */
public class NoriPlugin extends AbstractUIPlugin {

    public static final String PLUGIN_ID = "nori.ai.plugin";

    private static NoriPlugin plugin;

    @Override
    public void start(BundleContext context) throws Exception {
        super.start(context);
        plugin = this;

        // 기본 설정값 등록
        IPreferenceStore store = getPreferenceStore();
        store.setDefault(NoriConstants.PREF_SERVER_URL, NoriConstants.DEFAULT_SERVER_URL);
        store.setDefault(NoriConstants.PREF_API_KEY, "");
    }

    @Override
    public void stop(BundleContext context) throws Exception {
        plugin = null;
        super.stop(context);
    }

    public static NoriPlugin getDefault() {
        return plugin;
    }
}
