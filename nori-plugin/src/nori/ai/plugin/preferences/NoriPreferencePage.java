package nori.ai.plugin.preferences;

import org.eclipse.jface.preference.FieldEditorPreferencePage;
import org.eclipse.jface.preference.StringFieldEditor;
import org.eclipse.ui.IWorkbench;
import org.eclipse.ui.IWorkbenchPreferencePage;

import nori.ai.plugin.NoriConstants;
import nori.ai.plugin.NoriPlugin;

/**
 * Nori AI 설정 페이지 — Window > Preferences > Nori AI
 */
public class NoriPreferencePage extends FieldEditorPreferencePage
        implements IWorkbenchPreferencePage {

    public NoriPreferencePage() {
        super(GRID);
        setPreferenceStore(NoriPlugin.getDefault().getPreferenceStore());
        setDescription("Nori AI 서버 연결 설정");
    }

    @Override
    protected void createFieldEditors() {
        addField(new StringFieldEditor(
                NoriConstants.PREF_SERVER_URL,
                "서버 주소:",
                getFieldEditorParent()));

        addField(new StringFieldEditor(
                NoriConstants.PREF_API_KEY,
                "API Key (선택):",
                getFieldEditorParent()));
    }

    @Override
    public void init(IWorkbench workbench) {
        // 초기화 필요 없음 — 기본값은 NoriPlugin.start()에서 설정
    }
}
