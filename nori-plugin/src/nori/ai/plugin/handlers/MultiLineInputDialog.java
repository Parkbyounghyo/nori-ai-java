package nori.ai.plugin.handlers;

import org.eclipse.jface.dialogs.Dialog;
import org.eclipse.jface.dialogs.IDialogConstants;
import org.eclipse.swt.SWT;
import org.eclipse.swt.layout.GridData;
import org.eclipse.swt.layout.GridLayout;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Control;
import org.eclipse.swt.widgets.Label;
import org.eclipse.swt.widgets.Shell;
import org.eclipse.swt.widgets.Text;

/**
 * 멀티라인 입력을 지원하는 커스텀 다이얼로그.
 * Shift+Enter: 줄바꿈, Enter: 확인(OK)
 */
public class MultiLineInputDialog extends Dialog {

    private String title;
    private String message;
    private String initialValue;
    private String value;
    private Text textWidget;

    public MultiLineInputDialog(Shell parent, String title, String message,
                                 String initialValue) {
        super(parent);
        this.title = title;
        this.message = message;
        this.initialValue = initialValue != null ? initialValue : "";
        setShellStyle(getShellStyle() | SWT.RESIZE);
    }

    protected void configureShell(Shell shell) {
        super.configureShell(shell);
        shell.setText(title);
        shell.setSize(500, 250);
    }

    protected Control createDialogArea(Composite parent) {
        Composite area = (Composite) super.createDialogArea(parent);
        area.setLayout(new GridLayout(1, false));

        Label label = new Label(area, SWT.WRAP);
        label.setText(message);
        label.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false));

        textWidget = new Text(area, SWT.BORDER | SWT.MULTI | SWT.WRAP | SWT.V_SCROLL);
        GridData gd = new GridData(SWT.FILL, SWT.FILL, true, true);
        gd.heightHint = 100;
        textWidget.setLayoutData(gd);
        textWidget.setText(initialValue);

        // Shift+Enter: 줄바꿈 허용, Enter만: OK 버튼
        textWidget.addListener(SWT.KeyDown, new org.eclipse.swt.widgets.Listener() {
            public void handleEvent(org.eclipse.swt.widgets.Event e) {
                if (e.character == SWT.CR || e.character == SWT.LF) {
                    if ((e.stateMask & SWT.SHIFT) != 0) {
                        return; // Shift+Enter: 줄바꿈
                    }
                    e.doit = false;
                    okPressed();
                }
            }
        });

        return area;
    }

    protected void okPressed() {
        value = textWidget.getText();
        super.okPressed();
    }

    public String getValue() {
        return value;
    }
}
