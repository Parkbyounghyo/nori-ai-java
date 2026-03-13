package nori.ai.plugin.handlers;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

import org.eclipse.core.commands.AbstractHandler;
import org.eclipse.core.commands.ExecutionEvent;
import org.eclipse.core.commands.ExecutionException;
import org.eclipse.core.resources.IProject;
import org.eclipse.core.resources.IResource;
import org.eclipse.core.resources.ResourcesPlugin;
import org.eclipse.core.runtime.IProgressMonitor;
import org.eclipse.core.runtime.IStatus;
import org.eclipse.core.runtime.Status;
import org.eclipse.core.runtime.jobs.Job;
import org.eclipse.jface.dialogs.MessageDialog;
import org.eclipse.jface.text.ITextSelection;
import org.eclipse.jface.viewers.ISelection;
import org.eclipse.swt.widgets.Display;
import org.eclipse.swt.widgets.Shell;
import org.eclipse.ui.IEditorPart;
import org.eclipse.ui.IFileEditorInput;
import org.eclipse.ui.IWorkbenchPage;
import org.eclipse.ui.IWorkbenchPart;
import org.eclipse.ui.PlatformUI;
import org.eclipse.ui.handlers.HandlerUtil;
import org.eclipse.ui.texteditor.ITextEditor;

import nori.ai.plugin.NoriConstants;
import nori.ai.plugin.service.NoriApiClient;
import nori.ai.plugin.views.NoriSideView;

/**
 * 모든 Nori AI 명령의 통합 핸들러.
 * command ID로 어떤 기능인지 구분한다.
 */
public class NoriCommandHandler extends AbstractHandler {

    @Override
    public Object execute(ExecutionEvent event) throws ExecutionException {
        String commandId = event.getCommand().getId();
        Shell shell = HandlerUtil.getActiveShell(event);

        // ── 코드 생성: 선택 텍스트 불필요 ──
        if (NoriConstants.CMD_GENERATE.equals(commandId)) {
            MultiLineInputDialog dlg = new MultiLineInputDialog(shell, "코드 생성",
                    "생성할 코드를 설명해주세요 (Shift+Enter: 줄바꿈):", "");
            if (dlg.open() != MultiLineInputDialog.OK) return null;
            String description = dlg.getValue().trim();
            if (description.isEmpty()) return null;

            runAsync("코드 생성", () ->
                    NoriApiClient.getInstance().generateCode(description));
            return null;
        }

        // ── 현재 파일 AI 분석 업데이트 ──
        if (NoriConstants.CMD_PROFILE_UPDATE.equals(commandId)) {
            executeProfileUpdate(event);
            return null;
        }

        // ── DB 스키마 스캔 ──
        if (NoriConstants.CMD_SCHEMA_SCAN.equals(commandId)) {
            executeSchemaScan(event);
            return null;
        }

        // ── 에러 로그 추가 ──
        if (NoriConstants.CMD_ERROR_LOG.equals(commandId)) {
            MultiLineInputDialog dlg1 = new MultiLineInputDialog(shell, "\uc5d0\ub7ec \uae30\ub85d",
                    "\uc5d0\ub7ec \uc99d\uc0c1 (\uc5d0\ub7ec \uba54\uc2dc\uc9c0/\ud604\uc0c1):\n(Shift+Enter: \uc904\ubc14\uafc8)", "");
            if (dlg1.open() != MultiLineInputDialog.OK) return null;
            String symptom = dlg1.getValue().trim();
            if (symptom.isEmpty()) return null;

            MultiLineInputDialog dlg2 = new MultiLineInputDialog(shell, "\uc5d0\ub7ec \uae30\ub85d",
                    "\ud574\uacb0 \ubc29\ubc95:\n(Shift+Enter: \uc904\ubc14\uafc8)", "");
            if (dlg2.open() != MultiLineInputDialog.OK) return null;
            String solution = dlg2.getValue().trim();
            if (solution.isEmpty()) return null;

            MultiLineInputDialog dlg3 = new MultiLineInputDialog(shell, "\uc5d0\ub7ec \uae30\ub85d",
                    "\ubd84\ub958 (compile/runtime/config/db):", "runtime");
            if (dlg3.open() != MultiLineInputDialog.OK) return null;
            String category = dlg3.getValue().trim();

            runAsync("\uc5d0\ub7ec \uae30\ub85d", () ->
                    NoriApiClient.getInstance().addErrorLog(symptom, solution, category));
            return null;
        }

        // ── 코딩 컨벤션 저장 ──
        if (NoriConstants.CMD_CONVENTION.equals(commandId)) {
            MultiLineInputDialog dlg = new MultiLineInputDialog(shell, "\ucf54\ub529 \ucee8\ubca4\uc158",
                    "\ucee8\ubca4\uc158 \uaddc\uce59\uc744 \uc785\ub825\ud558\uc138\uc694 (\uc5ec\ub7ec \uac1c\ub294 |\ub85c \uad6c\ubd84):\n"
                    + "\uc608: \ub0a0\uc9dc\ubcc0\uc218\ub294 _dt\ub85c \ub05d\ub0b8\ub2e4|Slf4j \uc0ac\uc6a9|VO \ud074\ub798\uc2a4\ub294 Vo\ub85c \ub05d\ub0b8\ub2e4\n"
                    + "(Shift+Enter: \uc904\ubc14\uafc8)",
                    "");
            if (dlg.open() != MultiLineInputDialog.OK) return null;
            String input = dlg.getValue().trim();
            if (input.isEmpty()) return null;

            String[] parts = input.split("\\|");
            final List rules = new ArrayList();
            for (int i = 0; i < parts.length; i++) {
                String r = parts[i].trim();
                if (r.length() > 0) rules.add(r);
            }

            runAsync("\ucee8\ubca4\uc158 \uc800\uc7a5", () ->
                    NoriApiClient.getInstance().saveConvention(rules));
            return null;
        }

        // ── 에러 분석/수정: 에디터 + 콘솔 컨텍스트 모두 지원 ──
        if (NoriConstants.CMD_ERROR_ANALYZE.equals(commandId) || NoriConstants.CMD_ERROR_FIX.equals(commandId)) {
            String editorCode = getSelectedText(event);
            boolean fromConsole = false;
            String consoleText = null;

            if (editorCode == null || editorCode.trim().isEmpty()) {
                // 에디터에 선택된 코드가 없으면 콘솔 텍스트 확인
                consoleText = getConsoleSelectedText(event);
                if (consoleText == null || consoleText.trim().isEmpty()) {
                    MessageDialog.openWarning(shell, "Nori AI",
                            "에디터에서 코드를 선택하거나 콘솔에서 에러 텍스트를 선택해주세요.");
                    return null;
                }
                fromConsole = true;
            }

            if (fromConsole) {
                // 콘솔 선택 텍스트를 에러 메시지로 바로 사용
                final String errorMsg = consoleText;
                if (NoriConstants.CMD_ERROR_ANALYZE.equals(commandId)) {
                    runAsync("에러 분석", () ->
                            NoriApiClient.getInstance().analyzeError(errorMsg, ""));
                } else {
                    runAsync("자동 수정", () ->
                            NoriApiClient.getInstance().fixError(errorMsg, ""));
                }
            } else {
                // 에디터 코드가 있으면 에러 메시지 입력 다이얼로그 표시
                String dlgTitle = NoriConstants.CMD_ERROR_ANALYZE.equals(commandId) ? "에러 분석" : "자동 수정";
                MultiLineInputDialog dlg = new MultiLineInputDialog(shell, dlgTitle,
                        "에러 메시지를 입력하세요 (Shift+Enter: 줄바꿈):", "");
                if (dlg.open() != MultiLineInputDialog.OK) return null;
                String errorMsg = dlg.getValue();
                final String code = editorCode;
                if (NoriConstants.CMD_ERROR_ANALYZE.equals(commandId)) {
                    runAsync(dlgTitle, () ->
                            NoriApiClient.getInstance().analyzeError(errorMsg, code));
                } else {
                    runAsync(dlgTitle, () ->
                            NoriApiClient.getInstance().fixError(errorMsg, code));
                }
            }
            return null;
        }

        // ── 나머지 명령: 에디터에서 코드 선택 필요 ──
        String selectedText = getSelectedText(event);
        if (selectedText == null || selectedText.trim().isEmpty()) {
            MessageDialog.openWarning(shell, "Nori AI",
                    "먼저 에디터에서 코드를 선택해주세요.");
            return null;
        }

        // 추가 입력이 필요한 명령들
        if (NoriConstants.CMD_REFACTOR.equals(commandId)) {
            MultiLineInputDialog dlg = new MultiLineInputDialog(shell, "리팩토링",
                    "리팩토링 지시사항 (Shift+Enter: 줄바꿈):", "클린 코드로 개선해줘");
            if (dlg.open() != MultiLineInputDialog.OK) return null;
            String instruction = dlg.getValue();
            runAsync("리팩토링", () ->
                    NoriApiClient.getInstance().refactor(selectedText, instruction));
            return null;
        }

        // ── 단순 명령 (선택 코드만 사용) ──
        final String title;
        final String code = selectedText;

        switch (commandId) {
            case NoriConstants.CMD_EXPLAIN:
                title = "코드 설명";
                runAsync(title, () -> NoriApiClient.getInstance().explain(code));
                break;
            case NoriConstants.CMD_REVIEW:
                title = "코드 리뷰";
                runAsync(title, () -> NoriApiClient.getInstance().review(code));
                break;
            case NoriConstants.CMD_TEST_GENERATE:
                title = "테스트 생성";
                runAsync(title, () -> NoriApiClient.getInstance().generateTest(code));
                break;
            case NoriConstants.CMD_DOC_GENERATE:
                title = "JavaDoc 생성";
                runAsync(title, () -> NoriApiClient.getInstance().generateDoc(code));
                break;
            default:
                break;
        }

        return null;
    }

    /**
     * 콘솔 뷰에서 선택된 텍스트를 가져온다.
     */
    private String getConsoleSelectedText(ExecutionEvent event) {
        try {
            IWorkbenchPart part = HandlerUtil.getActivePart(event);
            if (part != null && part.getSite().getSelectionProvider() != null) {
                ISelection sel = part.getSite().getSelectionProvider().getSelection();
                if (sel instanceof ITextSelection) {
                    return ((ITextSelection) sel).getText();
                }
            }
        } catch (Exception e) {
            // 콘솔 텍스트를 가져올 수 없는 경우
        }
        return null;
    }

    /**
     * 활성 에디터에서 선택된 텍스트를 가져온다.
     */
    private String getSelectedText(ExecutionEvent event) {
        IEditorPart editor = HandlerUtil.getActiveEditor(event);
        if (editor == null) return null;

        // 직접 ITextEditor인 경우
        if (editor instanceof ITextEditor) {
            return extractSelection((ITextEditor) editor);
        }

        // 어댑터를 통한 접근 (멀티페이지 에디터 등)
        Object adapted = editor.getAdapter(ITextEditor.class);
        if (adapted instanceof ITextEditor) {
            return extractSelection((ITextEditor) adapted);
        }

        return null;
    }

    private String extractSelection(ITextEditor textEditor) {
        ISelection selection = textEditor.getSelectionProvider().getSelection();
        if (selection instanceof ITextSelection) {
            return ((ITextSelection) selection).getText();
        }
        return null;
    }

    /**
     * 비동기로 API를 호출하고 결과를 NoriSideView에 표시한다.
     */
    private void runAsync(final String title, final java.util.function.Supplier<String> task) {
        // 즉시 SideView를 열고 로딩 상태 표시
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                NoriSideView view = findOrOpenView();
                if (view != null) {
                    view.showLoading(title);
                } else {
                    // SideView를 열 수 없으면 상태바에 표시
                    try {
                        PlatformUI.getWorkbench().getActiveWorkbenchWindow()
                            .getShell().setText("Nori AI - " + title + " 처리 중...");
                    } catch (Exception ignore) {}
                }
            }
        });

        Job job = new Job("Nori AI - " + title) {
            @Override
            protected IStatus run(IProgressMonitor monitor) {
                monitor.beginTask(title + " - AI 분석 중...", IProgressMonitor.UNKNOWN);
                try {
                    final String result = task.get();
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            NoriSideView view = findOrOpenView();
                            if (view != null) {
                                view.showResult(title, result);
                            } else {
                                // SideView를 열 수 없으면 다이얼로그로 표시
                                try {
                                    Shell shell = PlatformUI.getWorkbench()
                                        .getActiveWorkbenchWindow().getShell();
                                    MessageDialog.openInformation(shell,
                                        "Nori AI - " + title,
                                        result != null && result.length() > 2000
                                            ? result.substring(0, 2000) + "\n\n... (결과가 잘렸습니다)"
                                            : result);
                                } catch (Exception ignore) {}
                            }
                        }
                    });
                } finally {
                    monitor.done();
                }
                return Status.OK_STATUS;
            }
        };
        job.setUser(true);   // Progress 다이얼로그에 표시
        job.schedule();
    }

    private NoriSideView findOrOpenView() {
        try {
            IWorkbenchPage page = PlatformUI.getWorkbench()
                    .getActiveWorkbenchWindow().getActivePage();
            if (page == null) return null;
            return (NoriSideView) page.showView(
                    NoriConstants.VIEW_ID, null,
                    IWorkbenchPage.VIEW_ACTIVATE);
        } catch (Exception e) {
            // 첫 시도 실패 시 다른 방법으로 재시도
            try {
                IWorkbenchPage page = PlatformUI.getWorkbench()
                        .getActiveWorkbenchWindow().getActivePage();
                if (page == null) return null;
                return (NoriSideView) page.showView(
                        NoriConstants.VIEW_ID, null,
                        IWorkbenchPage.VIEW_VISIBLE);
            } catch (Exception e2) {
                return null;
            }
        }
    }

    // ── 프로젝트 스캔 ──

    private void executeSchemaScan(ExecutionEvent event) {
        IProject project = getActiveProject(event);
        if (project == null) {
            Shell shell = HandlerUtil.getActiveShell(event);
            MessageDialog.openWarning(shell, "Nori AI",
                    "\ud504\ub85c\uc81d\ud2b8\ub97c \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.");
            return;
        }
        final File projectDir = project.getLocation().toFile();

        runAsync("DB \uc2a4\ud0a4\ub9c8 \uc2a4\uce94", new java.util.function.Supplier<String>() {
            public String get() {
                List sources = collectFiles(projectDir, new String[]{
                        "Vo.java", "VO.java", "Dto.java", "DTO.java",
                        "Entity.java", "_SQL.xml", "Mapper.xml", "mapper.xml"
                });
                if (sources.isEmpty()) {
                    return "VO/DTO/MyBatis XML \ud30c\uc77c\uc744 \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.";
                }
                return NoriApiClient.getInstance().scanSchema(sources);
            }
        });
    }

    private void executeApiScan(ExecutionEvent event) {
        IProject project = getActiveProject(event);
        if (project == null) {
            Shell shell = HandlerUtil.getActiveShell(event);
            MessageDialog.openWarning(shell, "Nori AI",
                    "\ud504\ub85c\uc81d\ud2b8\ub97c \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.");
            return;
        }
        final File projectDir = project.getLocation().toFile();

        runAsync("API \ub9e4\ud551 \uc2a4\uce94", new java.util.function.Supplier<String>() {
            public String get() {
                List sources = collectFiles(projectDir, new String[]{
                        "Controller.java", "Handler.java", "Resource.java"
                });
                if (sources.isEmpty()) {
                    return "Controller/Handler \ud30c\uc77c\uc744 \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.";
                }
                return NoriApiClient.getInstance().scanApi(sources);
            }
        });
    }

    /**
     * 프로젝트에서 특정 접미사를 가진 파일의 내용을 수집한다.
     */
    private List collectFiles(File dir, String[] suffixes) {
        List result = new ArrayList();
        collectFilesRecursive(dir, suffixes, result, 0);
        return result;
    }

    private void collectFilesRecursive(File dir, String[] suffixes, List result, int depth) {
        if (depth > 8 || dir == null || !dir.isDirectory()) return;
        if (result.size() >= 30) return; // 최대 30파일

        File[] children = dir.listFiles();
        if (children == null) return;

        for (int i = 0; i < children.length; i++) {
            File child = children[i];
            String name = child.getName();

            if (child.isDirectory()) {
                if (name.startsWith(".") || "target".equals(name)
                        || "build".equals(name) || "node_modules".equals(name)
                        || "bin".equals(name)) continue;
                collectFilesRecursive(child, suffixes, result, depth + 1);
            } else {
                for (int j = 0; j < suffixes.length; j++) {
                    if (name.endsWith(suffixes[j])) {
                        String content = readFileContent(child, 8000);
                        if (content.length() > 0) {
                            result.add("// === " + name + " ===\n" + content);
                        }
                        break;
                    }
                }
            }
        }
    }

    /** 현재 편집 중인 파일의 AI 설명을 프로필에 업데이트 */
    private void executeProfileUpdate(ExecutionEvent event) {
        // 현재 에디터에서 파일 경로 가져오기
        IEditorPart editor = HandlerUtil.getActiveEditor(event);
        if (editor == null || !(editor.getEditorInput() instanceof IFileEditorInput)) {
            Shell shell = HandlerUtil.getActiveShell(event);
            MessageDialog.openWarning(shell, "Nori AI",
                    "\uc5d0\ub514\ud130\uc5d0\uc11c \ud30c\uc77c\uc744 \uc5f4\uc5b4\uc8fc\uc138\uc694.");
            return;
        }

        IFileEditorInput fei = (IFileEditorInput) editor.getEditorInput();
        final IProject project = fei.getFile().getProject();
        final File projectDir = project.getLocation().toFile();
        final File targetFile = fei.getFile().getLocation().toFile();

        if (!targetFile.exists() || !targetFile.isFile()) {
            Shell shell = HandlerUtil.getActiveShell(event);
            MessageDialog.openWarning(shell, "Nori AI",
                    "\ud30c\uc77c\uc744 \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.");
            return;
        }

        // SideView에 위임
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                NoriSideView view = findOrOpenView();
                if (view != null) {
                    view.updateFileProfile(projectDir, targetFile);
                }
            }
        });
    }

    private IProject getActiveProject(ExecutionEvent event) {
        try {
            IEditorPart editor = HandlerUtil.getActiveEditor(event);
            if (editor != null && editor.getEditorInput() instanceof IFileEditorInput) {
                IResource res = ((IFileEditorInput) editor.getEditorInput()).getFile();
                return res.getProject();
            }
        } catch (Exception e) { /* ignore */ }

        // fallback: workspace 첫 번째 열린 프로젝트
        try {
            IProject[] projects = ResourcesPlugin.getWorkspace().getRoot().getProjects();
            for (int i = 0; i < projects.length; i++) {
                if (projects[i].isOpen()) return projects[i];
            }
        } catch (Exception e) { /* ignore */ }
        return null;
    }

    private static final Charset FILE_UTF8 = Charset.forName("UTF-8");

    private String readFileIfExists(File dir, String name) {
        File f = new File(dir, name);
        if (!f.exists() || !f.isFile()) return "";
        return readFileContent(f, 5000);
    }

    private String findAndRead(File dir, String targetName) {
        File found = findFile(dir, targetName, 5);
        if (found == null) return "";
        return readFileContent(found, 3000);
    }

    private File findFile(File dir, String name, int maxDepth) {
        if (maxDepth <= 0 || dir == null || !dir.isDirectory()) return null;
        File direct = new File(dir, name);
        if (direct.exists() && direct.isFile()) return direct;
        File[] children = dir.listFiles();
        if (children == null) return null;
        for (int i = 0; i < children.length; i++) {
            if (children[i].isDirectory()
                    && !children[i].getName().startsWith(".")
                    && !"target".equals(children[i].getName())
                    && !"build".equals(children[i].getName())
                    && !"node_modules".equals(children[i].getName())) {
                File r = findFile(children[i], name, maxDepth - 1);
                if (r != null) return r;
            }
        }
        return null;
    }

    private String readFileContent(File f, int maxLen) {
        try {
            BufferedReader br = new BufferedReader(
                    new InputStreamReader(new FileInputStream(f), FILE_UTF8));
            StringBuilder sb = new StringBuilder();
            char[] buf = new char[4096];
            int n;
            while ((n = br.read(buf)) != -1) {
                sb.append(buf, 0, n);
                if (sb.length() > maxLen) break;
            }
            br.close();
            if (sb.length() > maxLen) return sb.substring(0, maxLen);
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    private String buildFileTree(File dir, String indent, int depth) {
        if (depth > 4 || dir == null || !dir.isDirectory()) return "";
        StringBuilder sb = new StringBuilder();
        File[] children = dir.listFiles();
        if (children == null) return "";

        // skip dirs
        List skipDirs = new ArrayList();
        skipDirs.add("target");
        skipDirs.add("build");
        skipDirs.add("bin");
        skipDirs.add("node_modules");
        skipDirs.add(".git");
        skipDirs.add(".settings");
        skipDirs.add(".metadata");

        List skipExts = new ArrayList();
        skipExts.add(".class");
        skipExts.add(".jar");
        skipExts.add(".war");
        skipExts.add(".ear");

        Arrays.sort(children, new Comparator() {
            public int compare(Object a, Object b) {
                File fa = (File) a;
                File fb = (File) b;
                if (fa.isDirectory() && !fb.isDirectory()) return -1;
                if (!fa.isDirectory() && fb.isDirectory()) return 1;
                return fa.getName().compareToIgnoreCase(fb.getName());
            }
        });

        for (int i = 0; i < children.length; i++) {
            File child = children[i];
            String name = child.getName();
            if (name.startsWith(".") && depth == 0 && child.isDirectory()) continue;
            if (child.isDirectory()) {
                if (skipDirs.contains(name)) continue;
                sb.append(indent).append(name).append("/\n");
                sb.append(buildFileTree(child, indent + "  ", depth + 1));
            } else {
                boolean skip = false;
                for (int j = 0; j < skipExts.size(); j++) {
                    if (name.endsWith((String) skipExts.get(j))) {
                        skip = true;
                        break;
                    }
                }
                if (!skip) {
                    sb.append(indent).append(name).append("\n");
                }
            }
            if (sb.length() > 8000) {
                sb.append(indent).append("... (\uc774\ud558 \uc0dd\ub7b5)\n");
                break;
            }
        }
        return sb.toString();
    }
}
