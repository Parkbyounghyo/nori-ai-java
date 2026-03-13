package nori.ai.plugin.views;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.nio.charset.Charset;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;

import org.eclipse.core.resources.IProject;
import org.eclipse.core.resources.ResourcesPlugin;
import org.eclipse.core.runtime.IProgressMonitor;
import org.eclipse.core.runtime.IStatus;
import org.eclipse.core.runtime.Status;
import org.eclipse.core.runtime.jobs.Job;
import org.eclipse.jface.text.IDocument;
import org.eclipse.jface.text.ITextSelection;
import org.eclipse.jface.viewers.ISelection;
import org.eclipse.swt.SWT;
import org.eclipse.swt.browser.Browser;
import org.eclipse.swt.browser.BrowserFunction;
import org.eclipse.swt.custom.StyledText;
import org.eclipse.swt.graphics.Color;
import org.eclipse.swt.graphics.Font;
import org.eclipse.swt.layout.GridData;
import org.eclipse.swt.layout.GridLayout;
import org.eclipse.swt.widgets.Button;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Display;
import org.eclipse.swt.widgets.Label;
import org.eclipse.swt.widgets.Text;
import org.eclipse.ui.IEditorPart;
import org.eclipse.ui.IFileEditorInput;
import org.eclipse.ui.IWorkbenchPage;
import org.eclipse.ui.PlatformUI;
import org.eclipse.ui.part.ViewPart;
import org.eclipse.ui.texteditor.ITextEditor;

import nori.ai.plugin.NoriConstants;
import nori.ai.plugin.NoriPlugin;
import nori.ai.plugin.service.NoriApiClient;

/**
 * Nori AI 통합 사이드뷰 — 분석 결과 + 채팅이 하나의 대화 스트림.
 * AI 응답의 코드 블록은 [코드 적용] 버튼으로 에디터에 삽입/교체 가능.
 */
public class NoriSideView extends ViewPart {

    public static final String ID = NoriConstants.VIEW_ID;

    private static final Charset FILE_UTF8 = Charset.forName("UTF-8");
    private static final String PROFILE_FILENAME = ".nori-profile.md";

    /* ── UI 컴포넌트 ── */
    private Browser browser;
    private StyledText fallbackText;
    private boolean useBrowser = false;
    private Text chatInput;
    private Button ragCheck;
    private Button projectCheck;
    private Label statusLabel;

    private Font monoFont;
    private Color bgColor;
    private Color fgColor;

    /* ── 데이터 ── */
    // 각 항목: [0]=role("user"|"assistant"|"system"), [1]=content, [2]=title(또는 null)
    private final List messages = new ArrayList();
    private final List chatHistory = new ArrayList(); // 서버 전송용 순수 채팅 히스토리
    // 코드 적용용: AI 응답에서 추출한 코드 블록들
    private final List codeBlocks = new ArrayList();
    // 현재 채팅 세션 ID
    private volatile String currentSessionId = "";
    // 프로필 상태: 0=미분석(프로필 없음), 1=분석중, 2=완료
    private volatile int profileState = 0;
    private volatile long profileAnalysisStartTime = 0;
    private volatile boolean autoAnalysisTriggered = false;
    // 스트리밍/분석 멈춤 플래그
    private volatile boolean stopRequested = false;
    private volatile boolean isStreaming = false;
    // 멈춤 버튼
    private Button stopBtn;

    /* ── PL 확인 대기 상태 ── */
    private volatile String pendingMessage;
    private volatile String pendingSelectedCode;
    private volatile String pendingProfile;
    private volatile List pendingHistory;
    private volatile boolean pendingUseRag;
    private volatile File pendingProjectDir;
    private volatile String pendingTasksJson;
    private volatile String pendingTasksIntentLabel;
    private volatile List pendingNeededFiles;  // 전체 후보 파일 목록
    private volatile boolean pendingFallbackUsed;
    private volatile String pendingRagContext;

    public void createPartControl(Composite parent) {
        Display display = parent.getDisplay();
        bgColor = new Color(display, 30, 30, 30);
        fgColor = new Color(display, 212, 212, 212);
        monoFont = new Font(display, "Consolas", 10, SWT.NORMAL);

        Composite main = new Composite(parent, SWT.NONE);
        GridLayout mainLayout = new GridLayout(1, false);
        mainLayout.marginWidth = 0;
        mainLayout.marginHeight = 0;
        main.setLayout(mainLayout);

        /* ── 메인 대화 영역 (단일 통합 뷰) ── */
        try {
            browser = new Browser(main, SWT.NONE);
            browser.setLayoutData(new GridData(SWT.FILL, SWT.FILL, true, true));
            useBrowser = true;
            // JavaScript → Java 콜백: 코드 적용 버튼
            new BrowserFunction(browser, "applyCodeToEditor") {
                public Object function(Object[] args) {
                    if (args.length > 0) {
                        final int idx = ((Number) args[0]).intValue();
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                applyCode(idx);
                            }
                        });
                    }
                    return null;
                }
            };
            // JavaScript → Java 콜백: 코드 복사
            new BrowserFunction(browser, "copyCodeBlock") {
                public Object function(Object[] args) {
                    if (args.length > 0) {
                        final int idx = ((Number) args[0]).intValue();
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                copyCodeToClipboard(idx);
                            }
                        });
                    }
                    return null;
                }
            };
            // JavaScript → Java 콜백: 테스트 실행 버튼
            new BrowserFunction(browser, "runTestCode") {
                public Object function(Object[] args) {
                    if (args.length > 0) {
                        final int idx = ((Number) args[0]).intValue();
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                executeTest(idx);
                            }
                        });
                    }
                    return null;
                }
            };
            // JavaScript → Java 콜백: 프로젝트 갱신 버튼
            new BrowserFunction(browser, "refreshProjectAnalysis") {
                public Object function(Object[] args) {
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            startAIProjectAnalysis(true);
                        }
                    });
                    return null;
                }
            };
            // JavaScript → Java 콜백: 파일 열기 (경로, 옵션:시작라인)
            new BrowserFunction(browser, "openFileInProject") {
                public Object function(Object[] args) {
                    if (args.length > 0) {
                        final String filePath = String.valueOf(args[0]);
                        final int line = args.length > 1 && args[1] != null
                                ? (args[1] instanceof Number ? ((Number) args[1]).intValue() : Integer.parseInt(String.valueOf(args[1])))
                                : 0;
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                openProjectFile(filePath, line > 0 ? line : -1);
                            }
                        });
                    }
                    return null;
                }
            };

            // JavaScript → Java 콜백: 채팅 목록에서 세션 선택
            new BrowserFunction(browser, "loadChatSession") {
                public Object function(Object[] args) {
                    if (args.length > 0) {
                        final String sessionId = String.valueOf(args[0]);
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                loadSession(sessionId);
                            }
                        });
                    }
                    return null;
                }
            };
            // JavaScript → Java 콜백: 새 채팅 시작
            new BrowserFunction(browser, "startNewChatFromJs") {
                public Object function(Object[] args) {
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            startNewChat();
                        }
                    });
                    return null;
                }
            };
            // JavaScript → Java 콜백: 채팅 목록 패널 토글
            new BrowserFunction(browser, "toggleChatListPanel") {
                public Object function(Object[] args) {
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            showChatListInBrowser();
                        }
                    });
                    return null;
                }
            };
            // ── PL 워크플로우: nori:// URL 핸들러 ──
            browser.addLocationListener(new org.eclipse.swt.browser.LocationListener() {
                public void changing(org.eclipse.swt.browser.LocationEvent event) {
                    String url = event.location;
                    if (url != null && url.startsWith("nori://")) {
                        event.doit = false; // 실제 네비게이션 방지
                        handleNoriUrl(url);
                    }
                }
                public void changed(org.eclipse.swt.browser.LocationEvent event) {}
            });
        } catch (Throwable t) {
            if (browser != null && !browser.isDisposed()) browser.dispose();
            browser = null;
            useBrowser = false;
            fallbackText = new StyledText(main, SWT.MULTI | SWT.WRAP | SWT.V_SCROLL | SWT.READ_ONLY);
            fallbackText.setFont(monoFont);
            fallbackText.setBackground(bgColor);
            fallbackText.setForeground(fgColor);
            fallbackText.setLayoutData(new GridData(SWT.FILL, SWT.FILL, true, true));
        }

        /* ── 입력 바 ── */
        Composite inputBar = new Composite(main, SWT.NONE);
        inputBar.setLayout(new GridLayout(5, false));
        inputBar.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));

        ragCheck = new Button(inputBar, SWT.CHECK);
        ragCheck.setText("RAG");
        ragCheck.setSelection(true);
        ragCheck.setToolTipText("벡터DB 문서 참조 (RAG)");

        projectCheck = new Button(inputBar, SWT.CHECK);
        projectCheck.setText("\uD83D\uDCC2 프로젝트");
        projectCheck.setSelection(true);
        projectCheck.setToolTipText("현재 프로젝트 소스 코드를 AI에 전달");

        chatInput = new Text(inputBar, SWT.BORDER | SWT.MULTI | SWT.WRAP | SWT.V_SCROLL);
        GridData chatInputGd = new GridData(SWT.FILL, SWT.FILL, true, false);
        chatInputGd.heightHint = 40;
        chatInputGd.minimumHeight = 40;
        chatInput.setLayoutData(chatInputGd);
        chatInput.setMessage("메시지 입력 (Shift+Enter: 줄바꿈)");

        Button sendBtn = new Button(inputBar, SWT.PUSH);
        sendBtn.setText("전송");

        stopBtn = new Button(inputBar, SWT.PUSH);
        stopBtn.setText("\u25A0 멈춤");
        stopBtn.setEnabled(false);
        stopBtn.setToolTipText("AI 응답 생성 중단");
        stopBtn.addListener(SWT.Selection, new org.eclipse.swt.widgets.Listener() {
            public void handleEvent(org.eclipse.swt.widgets.Event e) {
                stopRequested = true;
            }
        });

        /* ── 상태 바 ── */
        statusLabel = new Label(main, SWT.NONE);
        statusLabel.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));
        statusLabel.setText("연결 확인 중...");

        /* ── 이벤트 ── */
        sendBtn.addListener(SWT.Selection, new org.eclipse.swt.widgets.Listener() {
            public void handleEvent(org.eclipse.swt.widgets.Event e) {
                sendChatMessage();
            }
        });
        chatInput.addListener(SWT.KeyDown, new org.eclipse.swt.widgets.Listener() {
            public void handleEvent(org.eclipse.swt.widgets.Event e) {
                if (e.character == SWT.CR || e.character == SWT.LF) {
                    if ((e.stateMask & SWT.SHIFT) != 0) {
                        // Shift+Enter: 줄바꿈 허용
                        return;
                    }
                    // Enter만: 전송
                    e.doit = false;
                    sendChatMessage();
                }
            }
        });
        // 내용 변경 시 입력란 높이 자동 조절
        chatInput.addListener(SWT.Modify, new org.eclipse.swt.widgets.Listener() {
            public void handleEvent(org.eclipse.swt.widgets.Event e) {
                int lineCount = chatInput.getLineCount();
                int lineH = chatInput.getLineHeight();
                int desired = Math.max(40, Math.min(lineCount * lineH + 10, 120));
                GridData gd = (GridData) chatInput.getLayoutData();
                if (gd.heightHint != desired) {
                    gd.heightHint = desired;
                    chatInput.getParent().layout(true);
                }
            }
        });

        refreshDisplay();
        checkConnection();
    }

    /* ═══════════════════════════════════════════════════════
     *  공개 API — NoriCommandHandler 에서 호출
     * ═══════════════════════════════════════════════════════ */

    /** 분석 결과를 대화 스트림에 추가 (우클릭 명령 결과) */
    public void showResult(final String title, final String content) {
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                messages.add(new String[]{"assistant", content != null ? content : "", title, nowTimestamp()});
                refreshDisplay();
            }
        });
    }

    /** 로딩 상태를 대화 스트림에 표시 — 사용자 요청으로 표기 */
    public void showLoading(final String title) {
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                messages.add(new String[]{"user", "\uD83D\uDCCB " + title + " \uC694\uCCAD", null, nowTimestamp()});
                messages.add(new String[]{"step", title + " \u2014 AI\uAC00 \uBD84\uC11D \uC911...", "step-menu-" + title.hashCode()});
                refreshDisplay();
            }
        });
    }

    /* ═══════════════════════════════════════════════════════
     *  채팅 발송
     * ═══════════════════════════════════════════════════════ */

    private void sendChatMessage() {
        final String message = chatInput.getText().trim();
        if (message.length() == 0) return;
        chatInput.setText("");

        final String selectedCode = getSelectedCode();
        final String fullMessage;
        final String displayMessage;

        if (selectedCode != null && selectedCode.length() > 0) {
            // 코드/파일 전달: message에는 사용자 입력만, 코드는 selected_code로 분리 전달
            fullMessage = message;
            String editorFile = getActiveEditorFileName();
            if (editorFile != null) {
                displayMessage = "\uD83D\uDCCE [" + editorFile + "] " + message;
            } else {
                String firstLine = selectedCode.split("\n")[0];
                if (firstLine.length() > 60) firstLine = firstLine.substring(0, 60) + "...";
                displayMessage = "\uD83D\uDCCE [" + firstLine + "] " + message;
            }
        } else {
            fullMessage = message;
            displayMessage = message;
        }

        // 프로젝트 디렉토리만 UI 스레드에서 가져오기 (SWT API 필요)
        final boolean useProject = projectCheck.getSelection();
        final File projectDir = useProject ? getActiveProjectDir() : null;

        messages.add(new String[]{"user", displayMessage, null, nowTimestamp()});
        chatHistory.add(new String[]{"user", message});
        refreshDisplay();

        final boolean useRag = ragCheck.getSelection();
        final List histSnapshot = new ArrayList(chatHistory);

        Job job = new Job("Nori AI - \ucc44\ud305") {
            protected IStatus run(IProgressMonitor monitor) {
                // 프로젝트 컨텍스트 수집 (백그라운드 스레드에서)
                final String projectContext;
                if (useProject && projectDir != null) {
                    addStepOnUI("step-context", "프로젝트 컨텍스트 수집 중...");
                    projectContext = collectProjectContextFromDir(projectDir);
                    completeStepOnUI("step-context", "프로젝트 컨텍스트 수집 완료 (" + projectContext.length() + "자)");
                } else {
                    projectContext = "";
                }
                if (projectContext != null && projectContext.length() > 0) {
                    // ═══ 스마트 라우팅 + 스트리밍 모드 ═══
                    doSmartChatStreaming(fullMessage, selectedCode,
                            projectContext, histSnapshot, useRag, projectDir);
                } else {
                    // ═══ 일반 채팅 모드 ═══
                    try {
                        final String response = NoriApiClient.getInstance()
                                .chat(fullMessage, histSnapshot, useRag, "");
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                messages.add(new String[]{"assistant", response, null, nowTimestamp()});
                                chatHistory.add(new String[]{"assistant", response});
                                refreshDisplay();
                            }
                        });
                    } catch (Exception ex) {
                        final String errMsg = ex.getMessage() != null ? ex.getMessage() : ex.getClass().getSimpleName();
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                messages.add(new String[]{"assistant",
                                        "\u274C \uc624\ub958: " + errMsg, null, nowTimestamp()});
                                refreshDisplay();
                            }
                        });
                    }
                }
                return Status.OK_STATUS;
            }
        };
        job.setUser(false);
        job.schedule();
    }

    /**
     * 스마트 라우팅 + SSE 스트리밍:
     * 1차) 의도분류 + 파일선별 (체크박스 진행 표시)
     * 2차) SSE 스트리밍으로 토큰 단위 응답 수신
     */
    private void doSmartChatStreaming(String message, String selectedCode,
                                      String profile, List history, boolean useRag,
                                      File activeProjectDir) {
      try {
        final NoriApiClient api = NoriApiClient.getInstance();

        // ── Step 1: 질문 분석 ──
        showLoadingOnUI("\u23F3 \uC9C8\uBB38 \uBD84\uC11D \uC911\u2026");

        String classifyResult = api.smartChatClassify(
                message, history, useRag, profile, selectedCode);

        if (classifyResult == null || classifyResult.startsWith("\uc5d0\ub7ec")
                || classifyResult.startsWith("\uc11c\ubc84")) {
            hideLoadingOnUI();
            final String fallback = api.chat(message, history, useRag, profile);
            addAssistantOnUI(fallback);
            return;
        }

        String phase = NoriApiClient.extractJsonField(classifyResult, "phase");
        if ("done".equals(phase)) {
            hideLoadingOnUI();
            String answer = NoriApiClient.extractJsonField(classifyResult, "answer");
            addAssistantOnUI(answer != null ? answer : classifyResult);
            return;
        }

        List neededFiles = extractJsonArray(classifyResult, "needed_files");
        String tasksJson = extractRawJsonArray(classifyResult, "tasks");
        String tasksIntentLabel = extractTasksSummary(classifyResult);

        // CLARIFY 의도
        if (tasksIntentLabel.contains("\u2753") || classifyResult.contains("\"CLARIFY\"")) {
            hideLoadingOnUI();
            String clarifyQ = extractClarifyDetail(classifyResult);
            addAssistantOnUI(clarifyQ != null ? clarifyQ
                    : "\uc870\uae08 \ub354 \uad6c\uccb4\uc801\uc73c\ub85c \ub9d0\uc94d\ud574\uc8fc\uc138\uc694.");
            return;
        }

        boolean fallbackUsed = classifyResult.contains("\"fallback_search\":true")
                || classifyResult.contains("\"fallback_search\": true");

        // ── 파일 발견 → 읽기 → 스트리밍 ──
        if (neededFiles.size() > 0) {
            showLoadingOnUI("\u23F3 \uD30C\uC77C " + neededFiles.size() + "\uAC1C \uC77D\uB294 \uC911\u2026");

            String fileContentsJson = readFilesAsJson(neededFiles, activeProjectDir);

            hideLoadingOnUI();
            executePLStreaming(message, selectedCode, profile, history, useRag,
                    activeProjectDir, tasksJson, fileContentsJson, fallbackUsed);
            return;
        }

        // 파일 없으면 바로 실행
        hideLoadingOnUI();
        executePLStreaming(message, selectedCode, profile, history, useRag,
                activeProjectDir, tasksJson, "{}", fallbackUsed);
      } catch (Exception e) {
        isStreaming = false;
        final String errMsg = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                if (stopBtn != null && !stopBtn.isDisposed()) stopBtn.setEnabled(false);
                failStepDirect("step-generate", "\uc624\ub958 \ubc1c\uc0dd");
                messages.add(new String[]{"assistant",
                        "\u274C \ucc98\ub9ac \uc911 \uc624\ub958: " + errMsg, null, nowTimestamp()});
                refreshDisplay();
            }
        });
      }
    }

    /** PL 확인 카드 HTML 생성 — 체크박스 + 진행/취소 버튼 */
    private String buildPlFileCardHtml(List files, String detailText, boolean fallbackUsed) {
        StringBuilder sb = new StringBuilder();
        sb.append("<div style='margin:6px 0;padding:10px;background:#1e293b;border:1px solid #475569;border-radius:8px;'>");
        sb.append("<div style='font-weight:bold;color:#60a5fa;margin-bottom:8px;'>");
        sb.append(fallbackUsed ? "\uD83D\uDD0D \uC720\uC0AC\uB3C4 \uAC80\uC0C9\uC73C\uB85C \uCC3E\uC740 \uD30C\uC77C" : "\uD83D\uDCC2 AI\uAC00 \uBD84\uC11D\uD55C \uAD00\uB828 \uD30C\uC77C");
        sb.append("</div>");
        if (detailText != null && detailText.length() > 0) {
            sb.append("<div style='color:#94a3b8;font-size:11px;margin-bottom:8px;'>" + escapeHtml(detailText) + "</div>");
        }
        sb.append("<div style='color:#cbd5e1;font-size:11px;margin-bottom:6px;'>\uD655\uC778 \uD6C4 \uC9C4\uD589\uD574\uC8FC\uC138\uC694. \uBD88\uD544\uC694\uD55C \uD30C\uC77C\uC740 \uCCB4\uD06C \uD574\uC81C\uD558\uC138\uC694.</div>");
        for (int i = 0; i < files.size(); i++) {
            String fpath = (String) files.get(i);
            int lastSlash = fpath.lastIndexOf('/');
            String fname = lastSlash >= 0 ? fpath.substring(lastSlash + 1) : fpath;
            sb.append("<label style='display:block;padding:3px 0;color:#e2e8f0;cursor:pointer;'>");
            sb.append("<input type='checkbox' checked id='plf_" + i + "' value='" + escapeHtml(fpath) + "' style='margin-right:6px;'>");
            sb.append("<span style='font-size:12px;'>" + escapeHtml(fname) + "</span>");
            sb.append("<span style='font-size:10px;color:#64748b;margin-left:6px;'>" + escapeHtml(fpath) + "</span>");
            sb.append("</label>");
        }
        sb.append("<div style='margin-top:10px;'>");
        sb.append("<a href='#' onclick=\"var fs=[];for(var i=0;i<" + files.size() + ";i++){var cb=document.getElementById('plf_'+i);if(cb&&cb.checked)fs.push(cb.value);}");
        sb.append("window.location='nori://pl-confirm?files='+encodeURIComponent(fs.join('|'));return false;\" ");
        sb.append("style='display:inline-block;padding:5px 16px;background:#2563eb;color:#fff;border-radius:4px;text-decoration:none;font-size:12px;margin-right:8px;'>");
        sb.append("\uC9C4\uD589</a>");
        sb.append("<a href='nori://pl-cancel' ");
        sb.append("style='display:inline-block;padding:5px 16px;background:#475569;color:#cbd5e1;border-radius:4px;text-decoration:none;font-size:12px;'>");
        sb.append("\uCDE8\uC18C</a>");
        sb.append("</div></div>");
        return sb.toString();
    }

    /** PL 확인 후 파일 읽기 + 스트리밍 실행 */
    private void handlePlConfirm(java.util.Map params) {
        String filesParam = (String) params.get("files");
        if (filesParam == null || pendingMessage == null) return;

        // 선택된 파일 파싱
        final java.util.List selectedFiles = new java.util.ArrayList();
        String[] parts = filesParam.split("\\|");
        for (int i = 0; i < parts.length; i++) {
            String f = parts[i].trim();
            if (f.length() > 0) selectedFiles.add(f);
        }

        // pending 상태에서 복원
        final String msg = pendingMessage;
        final String code = pendingSelectedCode;
        final String prof = pendingProfile;
        final List hist = pendingHistory;
        final boolean rag = pendingUseRag;
        final File projDir = pendingProjectDir;
        final String tasks = pendingTasksJson;
        final boolean fallback = pendingFallbackUsed;
        clearPendingState();

        // 백그라운드에서 파일 읽기 + 스트리밍
        Job job = new Job("Nori PL - \uD30C\uC77C \uC77D\uAE30 \uBC0F \uC2E4\uD589") {
            protected org.eclipse.core.runtime.IStatus run(org.eclipse.core.runtime.IProgressMonitor monitor) {
                try {
                    // 파일 읽기 단계 표시
                    StringBuilder fileListText = new StringBuilder();
                    for (int i = 0; i < selectedFiles.size(); i++) {
                        String fpath = (String) selectedFiles.get(i);
                        int lastSlash = fpath.lastIndexOf('/');
                        String fname = lastSlash >= 0 ? fpath.substring(lastSlash + 1) : fpath;
                        if (i > 0) fileListText.append(", ");
                        fileListText.append(fname);
                    }
                    String fileLabel = fallback
                            ? "\uC720\uC0AC\uB3C4 \uD30C\uC77C " + selectedFiles.size() + "\uAC1C \uC77D\uAE30: "
                            : "\uD30C\uC77C " + selectedFiles.size() + "\uAC1C \uC77D\uAE30: ";
                    addStepOnUI("step-files", fileLabel + fileListText.toString());

                    String fileContentsJson;
                    if (selectedFiles.size() > 0) {
                        fileContentsJson = readFilesAsJson(selectedFiles, projDir);
                    } else {
                        fileContentsJson = "{}";
                    }
                    int jsonLen = fileContentsJson.length();
                    boolean noFilesRead = jsonLen <= 2;
                    if (noFilesRead) {
                        failStepOnUI("step-files", "\uD30C\uC77C " + selectedFiles.size()
                                + "\uAC1C \uBABB \uCC3E\uC74C \u2014 \uD504\uB85C\uD544 \uAE30\uBC18 \uC751\uB2F5");
                    } else {
                        completeStepOnUI("step-files", "\uD30C\uC77C " + selectedFiles.size()
                                + "\uAC1C \uC77D\uAE30 \uC644\uB8CC (" + jsonLen + "B)");
                    }

                    executePLStreaming(msg, code, prof, hist, rag, projDir, tasks, fileContentsJson, fallback);
                } catch (Exception e) {
                    final String errMsg = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            failStepDirect("step-generate", "\uC624\uB958 \uBC1C\uC0DD");
                            messages.add(new String[]{"assistant",
                                    "\u274C \uCC98\uB9AC \uC911 \uC624\uB958: " + errMsg, null, nowTimestamp()});
                            refreshDisplay();
                        }
                    });
                }
                return org.eclipse.core.runtime.Status.OK_STATUS;
            }
        };
        job.setSystem(true);
        job.schedule();
    }

    /** PL 취소 */
    private void handlePlCancel() {
        clearPendingState();
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                messages.add(new String[]{"system", "\u274C \uC791\uC5C5\uC774 \uCDE8\uC18C\uB418\uC5C8\uC2B5\uB2C8\uB2E4.", null, nowTimestamp()});
                refreshDisplay();
            }
        });
    }

    /** 새 채팅 시작 — 대화 이력 초기화 */
    private void startNewChat() {
        if (isStreaming) return;
        messages.clear();
        chatHistory.clear();
        currentSessionId = "";
        clearPendingState();
        messages.add(new String[]{"system",
                "\uD83C\uDD95 \uC0C8\uB85C\uC6B4 \uB300\uD654\uAC00 \uC2DC\uC791\uB418\uC5C8\uC2B5\uB2C8\uB2E4.",
                null, nowTimestamp()});
        refreshDisplay();
    }

    /** 채팅 목록을 브라우저 내 HTML 오버레이로 표시 */
    private void showChatListInBrowser() {
        final NoriApiClient api = NoriApiClient.getInstance();
        new Thread(new Runnable() {
            public void run() {
                try {
                    String serverUrl = api.getServerUrl();
                    java.net.URL url = new java.net.URL(serverUrl + "/api/v1/sessions/list?user_id=default&limit=30");
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("GET");
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    conn.setRequestProperty("Accept", "application/json");
                    String apiKey = NoriPlugin.getDefault() != null
                            ? NoriPlugin.getDefault().getPreferenceStore().getString(NoriConstants.PREF_API_KEY) : "";
                    if (apiKey != null && !apiKey.isEmpty()) {
                        conn.setRequestProperty("X-API-Key", apiKey);
                    }
                    int status = conn.getResponseCode();
                    if (status != 200) return;
                    java.io.BufferedReader reader = new java.io.BufferedReader(
                            new java.io.InputStreamReader(conn.getInputStream(), Charset.forName("UTF-8")));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) sb.append(line);
                    reader.close();
                    final String json = sb.toString();
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            showChatListOverlay(json);
                        }
                    });
                } catch (Exception e) {
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            if (statusLabel != null && !statusLabel.isDisposed()) {
                                statusLabel.setText("\u274C 채팅 목록 불러오기 실패");
                            }
                        }
                    });
                }
            }
        }).start();
    }

    /** 채팅 목록 오버레이 HTML을 브라우저에 삽입 */
    private void showChatListOverlay(String json) {
        if (browser == null || browser.isDisposed()) return;
        // JSON 파싱
        java.util.List items = new java.util.ArrayList();
        int idx = 0;
        while (true) {
            int sIdx = json.indexOf("\"session_id\"", idx);
            if (sIdx < 0) break;
            String sid = extractJsonFieldFrom(json, sIdx, "session_id");
            String title = extractJsonFieldFrom(json, sIdx, "title");
            String updated = extractJsonFieldFrom(json, sIdx, "updated_at");
            String msgCount = extractJsonFieldFrom(json, sIdx, "message_count");
            if (sid == null) break;
            items.add(new String[]{sid, title, updated, msgCount});
            idx = sIdx + 10;
        }
        StringBuilder js = new StringBuilder();
        js.append("(function(){");
        js.append("var old=document.getElementById('nori-chatlist-overlay');if(old)old.parentNode.removeChild(old);");
        // 오버레이
        js.append("var ov=document.createElement('div');ov.id='nori-chatlist-overlay';");
        js.append("ov.style.cssText='position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:9999;display:flex;justify-content:center;align-items:center;';");
        // 박스
        js.append("var box=document.createElement('div');");
        js.append("box.style.cssText='background:#252526;border:1px solid #555;border-radius:8px;width:90%;max-width:400px;max-height:80%;overflow-y:auto;padding:16px;';");
        // 헤더
        js.append("var hdr=document.createElement('div');hdr.style.cssText='display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;';");
        js.append("var ttl=document.createElement('span');ttl.style.cssText='font-size:15px;font-weight:bold;color:#fff;';ttl.textContent='\\uD83D\\uDCCB 채팅 목록';");
        js.append("var cls=document.createElement('span');cls.style.cssText='cursor:pointer;color:#888;font-size:18px;padding:4px 8px;';cls.textContent='\\u2716';");
        js.append("cls.onclick=function(){ov.parentNode.removeChild(ov);};");
        js.append("hdr.appendChild(ttl);hdr.appendChild(cls);box.appendChild(hdr);");
        // 새 채팅 버튼
        js.append("var nb=document.createElement('div');nb.style.cssText='padding:8px 12px;margin-bottom:8px;background:#0e639c;color:#fff;border-radius:4px;cursor:pointer;text-align:center;font-size:13px;';");
        js.append("nb.textContent='\\u2795 새 채팅 시작';nb.onclick=function(){ov.parentNode.removeChild(ov);startNewChatFromJs();};box.appendChild(nb);");
        // 세션 리스트
        if (items.isEmpty()) {
            js.append("var em=document.createElement('div');em.style.cssText='color:#888;text-align:center;padding:20px;';em.textContent='저장된 채팅이 없습니다.';box.appendChild(em);");
        } else {
            for (int i = 0; i < items.size(); i++) {
                String[] item = (String[]) items.get(i);
                String sid = escapeJsStr(item[0]);
                String tit = (item[1] != null && item[1].length() > 0) ? escapeJsStr(item[1]) : "(제목 없음)";
                String date = (item[2] != null && item[2].length() >= 10) ? item[2].substring(0, 10) : "";
                String cnt = (item[3] != null) ? item[3] : "0";
                boolean isCurrent = currentSessionId != null && currentSessionId.equals(item[0]);
                String bg = isCurrent ? "#1a3a5c" : "#2d2d30";
                String bc = isCurrent ? "#569cd6" : "#444";
                js.append("{var it=document.createElement('div');it.style.cssText='padding:8px 12px;margin-bottom:4px;background:")
                  .append(bg).append(";border:1px solid ").append(bc).append(";border-radius:4px;cursor:pointer;';");
                js.append("var t1=document.createElement('div');t1.style.cssText='color:#e8e8e8;font-size:13px;';t1.textContent='").append(tit).append("';");
                js.append("var t2=document.createElement('div');t2.style.cssText='color:#888;font-size:11px;';t2.textContent='")
                  .append(date).append(" \\u00B7 ").append(cnt).append("개 메시지");
                if (isCurrent) js.append(" \\u00B7 현재");
                js.append("';");
                js.append("it.appendChild(t1);it.appendChild(t2);");
                js.append("it.onclick=(function(sid){return function(){ov.parentNode.removeChild(ov);loadChatSession(sid);};}('").append(sid).append("'));");
                js.append("box.appendChild(it);}");
            }
        }
        js.append("ov.appendChild(box);document.body.appendChild(ov);");
        js.append("ov.onclick=function(e){if(e.target===ov)ov.parentNode.removeChild(ov);};");
        js.append("})();");
        browser.execute(js.toString());
    }

    /** JS 문자열 이스케이프 (작은따옴표 내부용) */
    private static String escapeJsStr(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "");
    }

    /** 세션 로드 — 서버에서 메시지 가져와서 표시 */
    private void loadSession(final String sessionId) {
        final NoriApiClient api = NoriApiClient.getInstance();
        new Thread(new Runnable() {
            public void run() {
                try {
                    String serverUrl = api.getServerUrl();
                    java.net.URL url = new java.net.URL(serverUrl + "/api/v1/sessions/" + sessionId + "?user_id=default");
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("GET");
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    conn.setRequestProperty("Accept", "application/json");
                    String apiKey = NoriPlugin.getDefault() != null
                            ? NoriPlugin.getDefault().getPreferenceStore().getString(NoriConstants.PREF_API_KEY) : "";
                    if (apiKey != null && !apiKey.isEmpty()) {
                        conn.setRequestProperty("X-API-Key", apiKey);
                    }
                    int status = conn.getResponseCode();
                    if (status != 200) return;
                    java.io.BufferedReader reader = new java.io.BufferedReader(
                            new java.io.InputStreamReader(conn.getInputStream(), Charset.forName("UTF-8")));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) sb.append(line);
                    reader.close();
                    final String json = sb.toString();
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            restoreSessionFromJson(sessionId, json);
                        }
                    });
                } catch (Exception e) {
                    // 무시
                }
            }
        }).start();
    }

    /** JSON 응답에서 세션 메시지 복원 */
    private void restoreSessionFromJson(String sessionId, String json) {
        messages.clear();
        chatHistory.clear();
        currentSessionId = sessionId;
        // messages 배열에서 role, content 추출
        int idx = json.indexOf("\"messages\"");
        if (idx < 0) {
            refreshDisplay();
            return;
        }
        int arrStart = json.indexOf("[", idx);
        if (arrStart < 0) { refreshDisplay(); return; }
        int pos = arrStart;
        while (true) {
            int roleIdx = json.indexOf("\"role\"", pos);
            if (roleIdx < 0) break;
            String role = extractJsonFieldFrom(json, roleIdx, "role");
            String content = extractJsonFieldFrom(json, roleIdx, "content");
            if (role == null || content == null) break;
            messages.add(new String[]{role, content, null, ""});
            chatHistory.add(new String[]{role, content});
            pos = roleIdx + 10;
        }
        refreshDisplay();
    }

    /** JSON 문자열에서 특정 위치 이후의 필드 값 추출 */
    private static String extractJsonFieldFrom(String json, int startPos, String field) {
        String key = "\"" + field + "\"";
        int ki = json.indexOf(key, startPos);
        if (ki < 0) return null;
        int ci = json.indexOf(":", ki + key.length());
        if (ci < 0) return null;
        int vi = ci + 1;
        while (vi < json.length() && json.charAt(vi) == ' ') vi++;
        if (vi >= json.length()) return null;
        if (json.charAt(vi) == '"') {
            int end = vi + 1;
            while (end < json.length()) {
                char c = json.charAt(end);
                if (c == '\\') { end += 2; continue; }
                if (c == '"') break;
                end++;
            }
            return json.substring(vi + 1, end)
                    .replace("\\n", "\n")
                    .replace("\\t", "\t")
                    .replace("\\\"", "\"")
                    .replace("\\\\", "\\");
        }
        // number / boolean
        int end = vi;
        while (end < json.length() && json.charAt(end) != ',' && json.charAt(end) != '}' && json.charAt(end) != ']') end++;
        return json.substring(vi, end).trim();
    }

    /** pending 상태 초기화 */
    private void clearPendingState() {
        pendingMessage = null;
        pendingSelectedCode = null;
        pendingProfile = null;
        pendingHistory = null;
        pendingUseRag = false;
        pendingProjectDir = null;
        pendingTasksJson = null;
        pendingTasksIntentLabel = null;
        pendingNeededFiles = null;
        pendingFallbackUsed = false;
        pendingRagContext = null;
    }

    /** 파일 읽기 완료 후 스트리밍 실행 (공통) */
    private void executePLStreaming(final String message, final String selectedCode,
            final String profile, final List history, final boolean useRag,
            final File activeProjectDir, final String tasksJson,
            final String fileContentsJson, final boolean fallbackUsed) {
        // ── 스트리밍 시작 ──
        initStreamingDiv();

        stopRequested = false;
        isStreaming = true;
        final boolean[] stopFlag = new boolean[]{false};
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                if (stopBtn != null && !stopBtn.isDisposed()) stopBtn.setEnabled(true);
            }
        });

        final StringBuilder fullResponse = new StringBuilder();
        final NoriApiClient api = NoriApiClient.getInstance();

        api.streamSmartExecute(message, history, useRag, profile,
                selectedCode, fileContentsJson, tasksJson,
                currentSessionId,
                new NoriApiClient.StreamCallback() {
                    public void onStatus(final String msg) {
                        // UI 간소화: 상태 메시지 표시 안 함
                    }
                    public void onToken(final String content) {
                        if (stopRequested) { stopFlag[0] = true; return; }
                        fullResponse.append(content);
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                appendStreamText(content);
                            }
                        });
                    }
                    public void onDone(final String sessionId) {
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                if (sessionId != null && sessionId.length() > 0) {
                                    currentSessionId = sessionId;
                                }
                                if (stopBtn != null && !stopBtn.isDisposed()) stopBtn.setEnabled(false);
                                String response = fullResponse.toString();
                                if (response.trim().length() == 0) {
                                    response = "\u26A0\uFE0F AI \uBAA8\uB378\uC774 \uC751\uB2F5\uC744 \uC0DD\uC131\uD558\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4.";
                                }
                                applyHighlightToStream();
                                finalizeStreamDisplay();
                                messages.add(new String[]{"assistant",
                                        response, null, nowTimestamp()});
                                chatHistory.add(new String[]{"assistant",
                                        response});
                                isStreaming = false;
                            }
                        });
                    }
                    public void onError(final String error) {
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                if (stopBtn != null && !stopBtn.isDisposed()) stopBtn.setEnabled(false);
                                messages.add(new String[]{"assistant",
                                        "\u274C " + error, null, nowTimestamp()});
                                isStreaming = false;
                            }
                        });
                    }
                    public void onFileDone(final String filePath) {
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                applyHighlightToStream();
                            }
                        });
                    }
                    public void onFileStart(final String filePath, final int index, final int total, final int startLine) {
                        Display.getDefault().asyncExec(new Runnable() {
                            public void run() {
                                startNewFileSection(filePath, index, total, startLine);
                            }
                        });
                    }
                },
                stopFlag
        );
    }

    // ── UI 스레드 헬퍼 (체크박스 진행 표시) ──

    /** 진행 단계 추가 — ⬜ 상태로 시작 */
    private void addStepOnUI(final String stepId, final String msg) {
        Display.getDefault().syncExec(new Runnable() {
            public void run() {
                // step: 형태로 메시지 저장 (렌더링 시 체크박스 구분)
                messages.add(new String[]{"step", msg, stepId});
                refreshDisplay();
            }
        });
    }

    /** 단계 완료 — ✅ 상태로 전환 (백그라운드 스레드용) */
    private void completeStepOnUI(final String stepId, final String msg) {
        Display.getDefault().syncExec(new Runnable() {
            public void run() {
                completeStepDirect(stepId, msg);
            }
        });
    }

    /** 단계 완료 — ✅ 상태로 전환 (UI 스레드 직접 호출) */
    private void completeStepDirect(String stepId, String msg) {
        for (int i = messages.size() - 1; i >= 0; i--) {
            String[] m = (String[]) messages.get(i);
            if ("step".equals(m[0]) && stepId.equals(m[2])) {
                m[0] = "step-done";
                m[1] = msg;
                break;
            }
        }
        if (isStreaming) {
            updateStepInDom(stepId, msg, "\u2705", "step-done");
        } else {
            refreshDisplay();
        }
    }

    /** 단계 실패 — ❌ 상태로 전환 (백그라운드 스레드용) */
    private void failStepOnUI(final String stepId, final String msg) {
        Display.getDefault().syncExec(new Runnable() {
            public void run() {
                failStepDirect(stepId, msg);
            }
        });
    }

    /** 단계 실패 — ❌ 상태로 전환 (UI 스레드 직접 호출) */
    private void failStepDirect(String stepId, String msg) {
        for (int i = messages.size() - 1; i >= 0; i--) {
            String[] m = (String[]) messages.get(i);
            if ("step".equals(m[0]) && stepId.equals(m[2])) {
                m[0] = "step-fail";
                m[1] = msg;
                break;
            }
        }
        if (isStreaming) {
            updateStepInDom(stepId, msg, "\u274C", "step-fail");
        } else {
            refreshDisplay();
        }
    }

    /** 스트리밍 중 step 요소를 DOM에서 직접 갱신 (refreshDisplay 없이) */
    private void updateStepInDom(String stepId, String msg, String icon, String cssClass) {
        if (!useBrowser || browser == null || browser.isDisposed()) return;
        String escaped = escapeForJs(msg);
        String js = "(function(){"
                + "var steps=document.querySelectorAll('.step');"
                + "for(var i=steps.length-1;i>=0;i--){"
                +   "var t=steps[i].querySelector('.step-text');"
                +   "if(t){"
                +     "steps[i].className='step " + cssClass + "';"
                +     "var ic=steps[i].querySelector('.step-icon');"
                +     "if(ic)ic.textContent='" + icon + "';"
                +     "t.textContent='" + escaped + "';"
                +     "break;"
                +   "}"
                + "}"
                + "})();";
        browser.execute(js);
    }

    private void addStatusOnUI(final String msg) {
        Display.getDefault().syncExec(new Runnable() {
            public void run() {
                messages.add(new String[]{"system", msg, null});
                if (isStreaming) {
                    insertStatusDirect(msg);
                } else {
                    refreshDisplay();
                }
            }
        });
    }

    private void insertStatusDirect(String msg) {
        if (!useBrowser || browser == null || browser.isDisposed()) return;
        String escaped = escapeForJs(msg);
        String js = "(function(){var box=document.getElementById('nori-stream-box');"
                + "if(!box)return;"
                + "var d=document.createElement('div');d.className='thinking';"
                + "d.textContent='" + escaped + "';"
                + "box.parentNode.insertBefore(d,box);"
                + "if(window.__noriAutoScroll){window.scrollTo(0,document.body.scrollHeight);}"
                + "})();";
        browser.execute(js);
    }

    private void addAssistantOnUI(final String response) {
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                messages.add(new String[]{"assistant", response, null, nowTimestamp()});
                chatHistory.add(new String[]{"assistant", response});
                refreshDisplay();
            }
        });
    }

    /** JSON 라우팅 결과에서 intent들을 요약 문자열로 추출 (한국어 라벨 기준 중복 제거) */
    private String extractTasksSummary(String json) {
        List intents = extractJsonArray(json, "intent");
        if (intents.isEmpty()) {
            // tasks 배열에서 intent 값 직접 탐색
            List found = new ArrayList();
            String search = "\"intent\"";
            int pos = 0;
            while (true) {
                int idx = json.indexOf(search, pos);
                if (idx < 0) break;
                int colonIdx = json.indexOf(':', idx + search.length());
                if (colonIdx < 0) break;
                int qStart = json.indexOf('"', colonIdx + 1);
                if (qStart < 0) break;
                int qEnd = json.indexOf('"', qStart + 1);
                if (qEnd < 0) break;
                String intent = json.substring(qStart + 1, qEnd);
                if (!found.contains(intent)) found.add(intent);
                pos = qEnd + 1;
            }
            // 한국어 라벨 기준으로 중복 제거
            List labels = new ArrayList();
            for (int i = 0; i < found.size(); i++) {
                String label = intentToKorean((String) found.get(i));
                if (!labels.contains(label)) labels.add(label);
            }
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < labels.size(); i++) {
                if (i > 0) sb.append(", ");
                sb.append((String) labels.get(i));
            }
            return sb.length() > 0 ? sb.toString() : "분석";
        }
        // 한국어 라벨 기준 중복 제거
        List labels = new ArrayList();
        for (int i = 0; i < intents.size(); i++) {
            String label = intentToKorean((String) intents.get(i));
            if (!labels.contains(label)) labels.add(label);
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < labels.size(); i++) {
            if (i > 0) sb.append(", ");
            sb.append((String) labels.get(i));
        }
        return sb.toString();
    }

    private String intentToKorean(String intent) {
        if ("EXPLAIN".equals(intent)) return "\uD83D\uDCD6 코드 설명";
        if ("REVIEW".equals(intent)) return "\uD83D\uDD0D 코드 리뷰";
        if ("GENERATE".equals(intent)) return "\u2728 코드 생성";
        if ("REFACTOR".equals(intent)) return "\uD83D\uDD27 리팩토링";
        if ("ERROR_FIX".equals(intent)) return "\uD83D\uDEE0 에러 수정";
        if ("ERROR_ANALYZE".equals(intent)) return "\uD83D\uDD25 에러 분석";
        if ("GENERATE_DOC".equals(intent)) return "\uD83D\uDCDD 문서 생성";
        if ("GENERATE_TEST".equals(intent)) return "\uD83E\uDDEA 테스트 생성";
        if ("SEARCH".equals(intent)) return "\uD83D\uDD0E 검색";
        if ("CLARIFY".equals(intent)) return "\u2753 확인 질문";
        return "\uD83D\uDCAC 답변";
    }

    /** CLARIFY intent의 detail 메시지 추출 */
    private String extractClarifyDetail(String json) {
        // "detail" 필드에서 되물어볼 질문을 추출
        String detail = NoriApiClient.extractJsonField(json, "detail");
        if (detail != null && detail.length() > 0) {
            return detail;
        }
        return null;
    }

    /** JSON 문자열에서 특정 키의 배열을 raw JSON 문자열로 추출 */
    private String extractRawJsonArray(String json, String key) {
        String search = "\"" + key + "\"";
        int idx = json.indexOf(search);
        if (idx < 0) return "[]";
        int bracketStart = json.indexOf('[', idx + search.length());
        if (bracketStart < 0) return "[]";
        int depth = 0;
        for (int i = bracketStart; i < json.length(); i++) {
            char c = json.charAt(i);
            if (c == '[') depth++;
            else if (c == ']') {
                depth--;
                if (depth == 0) {
                    return json.substring(bracketStart, i + 1);
                }
            }
        }
        return "[]";
    }

    /** tasks 배열에서 detail 내용들을 추출하여 요약 문자열로 반환 */
    private String extractTasksDetailText(String json) {
        StringBuilder sb = new StringBuilder();
        String search = "\"detail\"";
        int pos = 0;
        while (true) {
            int idx = json.indexOf(search, pos);
            if (idx < 0) break;
            int colonIdx = json.indexOf(':', idx + search.length());
            if (colonIdx < 0) break;
            int qStart = json.indexOf('"', colonIdx + 1);
            if (qStart < 0) break;
            int qEnd = json.indexOf('"', qStart + 1);
            if (qEnd < 0) break;
            String detail = json.substring(qStart + 1, qEnd);
            if (detail.length() > 0) {
                if (sb.length() > 0) sb.append(", ");
                // 너무 긴 detail은 잘라서 표시
                if (detail.length() > 50) {
                    sb.append(detail.substring(0, 50)).append("...");
                } else {
                    sb.append(detail);
                }
            }
            pos = qEnd + 1;
        }
        return sb.toString();
    }

    // ── 스트리밍 디스플레이 (JavaScript 기반 실시간 업데이트) ──

    private void initStreamingDiv() {
        Display.getDefault().syncExec(new Runnable() {
            public void run() {
                if (!useBrowser || browser == null || browser.isDisposed()) return;
                String js = "window.__noriAutoScroll=true;"
                        + "window.__noriStreamConverted=false;"
                        + "window.__noriCurrentFile=null;"
                        + "var d=document.createElement('div');"
                        + "d.className='nori-response';"
                        + "d.id='nori-stream-box';"
                        + "d.innerHTML='<pre id=\"nori-stream\"></pre>';"
                        + "document.querySelector('.chat-wrap').appendChild(d);"
                        + "window.addEventListener('wheel',function(e){"
                        + "if(e.deltaY<0){window.__noriAutoScroll=false;}});"
                        + "window.addEventListener('scroll',function(){"
                        + "if((window.innerHeight+window.pageYOffset)>=document.body.scrollHeight-30){"
                        + "window.__noriAutoScroll=true;}});"
                        + "window.scrollTo(0,document.body.scrollHeight);";
                browser.execute(js);
            }
        });
    }

    /** 분석/파일 읽기 중 로딩 표시 (스트리밍 전 단계) */
    private void showLoadingOnUI(final String text) {
        Display.getDefault().syncExec(new Runnable() {
            public void run() {
                if (!useBrowser || browser == null || browser.isDisposed()) return;
                String escaped = escapeForJs(text);
                String js = "(function(){"
                        + "var el=document.getElementById('nori-loading');"
                        + "if(!el){"
                        +   "el=document.createElement('div');"
                        +   "el.id='nori-loading';"
                        +   "el.style.cssText='color:#888;font-size:12px;padding:6px 0;';"
                        +   "document.querySelector('.chat-wrap').appendChild(el);"
                        + "}"
                        + "el.textContent='" + escaped + "';"
                        + "window.scrollTo(0,document.body.scrollHeight);"
                        + "})();";
                browser.execute(js);
            }
        });
    }

    /** 로딩 표시 제거 */
    private void hideLoadingOnUI() {
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                if (!useBrowser || browser == null || browser.isDisposed()) return;
                browser.execute("var el=document.getElementById('nori-loading');if(el)el.parentNode.removeChild(el);");
            }
        });
    }

    private void appendStreamText(String token) {
        if (!useBrowser || browser == null || browser.isDisposed()) return;
        String escaped = escapeForJs(token);
        String js = "(function(){"
                + "var el=document.getElementById('nori-stream');"
                + "if(!el)return;"
                + "var t='" + escaped + "';"
                + "el.appendChild(document.createTextNode(t));"
                + "if(window.__noriAutoScroll){window.scrollTo(0,document.body.scrollHeight);}"
                + "})();";
        browser.execute(js);
    }

    /** PL 턴제: 파일 1개 완료 시 스트림 텍스트를 HTML로 변환하고 코드 하이라이트 적용 */
    private void applyHighlightToStream() {
        if (!useBrowser || browser == null || browser.isDisposed()) return;
        String js =
            "(function(){"
          + "var el=document.getElementById('nori-stream');"
          + "if(!el)return;"
          + "if(window.__noriStreamConverted){return;}"
          + "var raw=el.textContent||'';"
          + "if(raw.trim().length===0){window.__noriStreamConverted=true;return;}"
          + "function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}"
          + "var cf=window.__noriCurrentFile||{};"
          + "var fp=cf.path||'',sl=cf.line||1;"
          + "var fn=fp.split('/').pop()||'';"
          // 코드블록 파싱
          + "var parts=raw.split(/(```[a-z]*\\n[\\s\\S]*?```)/g);"
          + "var html='';"
          + "for(var i=0;i<parts.length;i++){"
          +   "var p=parts[i];"
          +   "var m=p.match(/^```([a-z]*)\\n([\\s\\S]*?)```$/);"
          +   "if(m){"
          +     "var lang=m[1]||'java',code=esc(m[2]);"
          +     "code=code.replace(/\\/\\/ \u2605 \uCD94\uAC00/g,'<span class=\"hl-add\">$&</span>');"
          +     "code=code.replace(/\\/\\/ \u2605 \uC218\uC815/g,'<span class=\"hl-mod\">$&</span>');"
          +     "if(fp){"
          +       "var sp=fp.replace(/'/g,\"\\\\'\");"
          +       "html+='<div class=\"nori-source-card\" data-file-path=\"'+esc(fp)+'\">';"
          +       "html+='<div class=\"nori-file-info\">';"
          +       "html+='<div class=\"file-name\">\uD83D\uDCC4 '+esc(fn)+'</div>';"
          +       "html+='<a class=\"file-path\" href=\"#\" onclick=\"openFileInProject(\\''+sp+'\\','+sl+');return false;\">\uD83D\uDCC2 '+esc(fp)+'</a>';"
          +       "html+='</div>';"
          +       "html+='<div class=\"nori-source-box\"><pre><code class=\"language-'+lang+'\">'+code+'</code></pre></div>';"
          +       "html+='<div class=\"nori-source-actions\">';"
          +       "html+='<button class=\"copy-btn\" onclick=\"copySourceFromCard(this)\">\uD83D\uDCCB \uBCF5\uC0AC</button>';"
          +       "html+='</div></div>';"
          +     "}else{"
          +       "html+='<pre><code class=\"language-'+lang+'\">'+code+'</code></pre>';"
          +     "}"
          +   "}else{"
          +     "var t=esc(p);"
          +     "t=t.replace(/### \uD83D\uDCC4[^\\n]*/g,'');"
          +     "t=t.replace(/`([^`]+)`/g,'<code>$1</code>');"
          +     "t=t.replace(/^## (.+)$/gm,'<h3>$1</h3>');"
          +     "t=t.replace(/^### (.+)$/gm,'<h4>$1</h4>');"
          +     "t=t.replace(/^&gt; (.+)$/gm,'<blockquote>$1</blockquote>');"
          +     "t=t.replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>');"
          +     "t=t.replace(/\\n/g,'<br>');"
          +     "t=t.replace(/^(<br>)+/,'').replace(/(<br>)+$/,'').trim();"
          +     "if(t.length>0)html+='<div>'+t+'</div>';"
          +   "}"
          + "}"
          + "el.style.whiteSpace='normal';"
          + "el.innerHTML=html;"
          + "window.__noriStreamConverted=true;"
          + "if(typeof hlJava!=='undefined'){"
          +   "var blocks=el.querySelectorAll('pre code');"
          +   "for(var b=0;b<blocks.length;b++){"
          +     "var cel=blocks[b],cls=cel.className||'',src=cel.textContent||'';"
          +     "var lm=cls.match(/language-([a-z]+)/);"
          +     "var lg=lm?lm[1]:'';"
          +     "if(lg==='xml'||lg==='html'||lg==='jsp')cel.innerHTML=hlXml(src);"
          +     "else if(lg==='sql')cel.innerHTML=hlSql(src);"
          +     "else cel.innerHTML=hlJava(src);"
          +   "}"
          + "}"
          + "if(window.__noriAutoScroll){window.scrollTo(0,document.body.scrollHeight);}"
          + "})();";
        browser.execute(js);
    }

    /** 스트리밍 중 청크 상태를 DOM에 직접 삽입 — refreshDisplay 호출 없이 */
    private void insertChunkStatusDirect(String chunkMsg) {
        if (!useBrowser || browser == null || browser.isDisposed()) return;
        String[] lines = chunkMsg.split("\\n");
        StringBuilder jsLines = new StringBuilder();
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i].trim();
            if (line.length() > 0) {
                jsLines.append("var s").append(i).append("=document.createElement('div');");
                jsLines.append("s").append(i).append(".className='step step-done';");
                jsLines.append("s").append(i).append(".innerHTML='<span class=\"step-icon\">\u2705</span><span class=\"step-text\">")
                       .append(escapeForJs(line)).append("</span>';");
                jsLines.append("box.parentNode.insertBefore(s").append(i).append(",box);");
            }
        }
        if (jsLines.length() == 0) return;
        String js = "(function(){var box=document.getElementById('nori-stream-box');"
                + "if(!box)return;"
                + jsLines.toString()
                + "if(window.__noriAutoScroll){window.scrollTo(0,document.body.scrollHeight);}"
                + "})();";
        browser.execute(js);
    }

    /** 스트리밍 완료 후 경량 마무리 */
    private void finalizeStreamDisplay() {
        if (!useBrowser || browser == null || browser.isDisposed()) return;
        String js =
            "(function(){"
          + "var el=document.getElementById('nori-stream');"
          + "if(el){"
          +   "var prev=el.innerHTML||el.textContent||'';"
          +   "if(prev.trim().length>0){"
          +     "var pdiv=document.createElement('div');"
          +     "pdiv.className='nori-file-result';"
          +     "pdiv.innerHTML=prev;"
          +     "el.parentNode.insertBefore(pdiv,el);"
          +   "}"
          +   "el.style.display='none';"
          + "}"
          + "window.scrollTo(0,document.body.scrollHeight);"
          + "})();";
        browser.execute(js);
    }

    /** PL 턴제: 새 파일 시작 시 이전 내용을 영구 div로 이동하고 구분선 삽입 */
    private void startNewFileSection(String filePath, int index, int total, int startLine) {
        if (!useBrowser || browser == null || browser.isDisposed()) return;
        String escapedPath = escapeForJs(filePath);
        String js =
            "(function(){"
          + "var el=document.getElementById('nori-stream');"
          + "if(!el)return;"
          + "window.__noriCurrentFile={path:'" + escapedPath + "',line:" + startLine + "};"
          + "if(" + index + ">0){"
          +   "var prev=el.innerHTML||el.textContent||'';"
          +   "if(prev.trim().length>0){"
          +     "var pdiv=document.createElement('div');"
          +     "pdiv.innerHTML=prev;"
          +     "el.parentNode.insertBefore(pdiv,el);"
          +   "}"
          +   "el.textContent='';"
          +   "el.style.whiteSpace='pre-wrap';"
          +   "window.__noriStreamConverted=false;"
          +   "var hr=document.createElement('hr');"
          +   "el.parentNode.insertBefore(hr,el);"
          + "}"
          + "if(window.__noriAutoScroll){window.scrollTo(0,document.body.scrollHeight);}"
          + "})();";
        browser.execute(js);
    }

    private void updateStreamStatus(String msg) {
        // noop — 채팅 버블 제거로 더 이상 상태 헤더 없음
    }

    private static String escapeForJs(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    /** JSON 응답에서 문자열 배열 추출 (needed_files) */
    private List extractJsonArray(String json, String fieldName) {
        List result = new ArrayList();
        String key = "\"" + fieldName + "\"";
        int keyIdx = json.indexOf(key);
        if (keyIdx < 0) return result;

        int arrStart = json.indexOf("[", keyIdx);
        if (arrStart < 0) return result;
        int arrEnd = json.indexOf("]", arrStart);
        if (arrEnd < 0) return result;

        String arrContent = json.substring(arrStart + 1, arrEnd);
        // "path1","path2" 형태에서 각 값 추출
        int pos = 0;
        while (pos < arrContent.length()) {
            int qStart = arrContent.indexOf('"', pos);
            if (qStart < 0) break;
            int qEnd = arrContent.indexOf('"', qStart + 1);
            if (qEnd < 0) break;
            String val = arrContent.substring(qStart + 1, qEnd);
            if (val.length() > 0) result.add(val);
            pos = qEnd + 1;
        }
        return result;
    }

    /** 파일 목록을 로컬에서 읽어서 {"경로":"내용"} JSON 맵 생성 */
    private String readFilesAsJson(List filePaths, File activeProjectDir) {
        try {
            return readFilesAsJsonInternal(filePaths, activeProjectDir);
        } catch (Exception ex) {
            String errMsg = "파일 읽기 오류: " + ex.getClass().getSimpleName() + ": " + ex.getMessage();
            System.out.println("[NORI-FILE] EXCEPTION: " + errMsg);
            ex.printStackTrace();
            addStepOnUI("step-file-err", errMsg);
            return "{}";
        }
    }

    private String readFilesAsJsonInternal(List filePaths, File activeProjectDir) {
        // ── 1. 검색 대상 디렉토리 수집 (우선순위 순) ──
        List searchDirs = new ArrayList();  // File 목록

        // 1-1) 활성 프로젝트 디렉토리
        if (activeProjectDir != null && activeProjectDir.isDirectory()) {
            searchDirs.add(activeProjectDir);
        }

        // 1-2) Eclipse 워크스페이스의 모든 열린 프로젝트
        try {
            org.eclipse.core.resources.IProject[] projects =
                    org.eclipse.core.resources.ResourcesPlugin.getWorkspace().getRoot().getProjects();
            for (int p = 0; p < projects.length; p++) {
                if (projects[p].isOpen() && projects[p].getLocation() != null) {
                    File d = projects[p].getLocation().toFile();
                    if (d.isDirectory() && !searchDirs.contains(d)) {
                        searchDirs.add(d);
                    }
                }
            }
        } catch (Exception e) {
            System.out.println("[NORI-FILE] 프로젝트 목록 조회 실패: " + e.getMessage());
        }

        // 1-3) 최종 fallback: 워크스페이스 루트 디렉토리
        if (searchDirs.isEmpty()) {
            try {
                org.eclipse.core.runtime.IPath wsLoc =
                        org.eclipse.core.resources.ResourcesPlugin.getWorkspace().getRoot().getLocation();
                if (wsLoc != null) {
                    File wsDir = wsLoc.toFile();
                    if (wsDir.isDirectory()) searchDirs.add(wsDir);
                }
            } catch (Exception e) { /* ignore */ }
        }

        // 디버그 정보 수집
        StringBuilder dirNames = new StringBuilder();
        for (int d = 0; d < searchDirs.size(); d++) {
            File pd = (File) searchDirs.get(d);
            if (d > 0) dirNames.append(", ");
            dirNames.append(pd.getName());
            System.out.println("[NORI-FILE] searchDir[" + d + "]=" + pd.getAbsolutePath());
        }
        System.out.println("[NORI-FILE] 찾을 파일: " + filePaths);

        if (searchDirs.isEmpty()) {
            addStepOnUI("step-file-err", "검색 디렉토리 없음! (activeProjectDir="
                    + (activeProjectDir != null ? activeProjectDir.getAbsolutePath() : "null") + ")");
            return "{}";
        }

        // ── 2. 각 파일 검색 (전략: 파일명 우선 검색) ──
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        int count = 0;
        StringBuilder debugLog = new StringBuilder();

        for (int i = 0; i < filePaths.size() && count < 15; i++) {
            String path = ((String) filePaths.get(i)).trim();
            if (path.length() == 0) continue;

            // 파일명 추출
            String fileName = path;
            int lastSlash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
            if (lastSlash >= 0) fileName = path.substring(lastSlash + 1);

            File f = null;
            String matchType = null;

            // ★ 전략 1: 파일명으로 직접 재귀 검색 (가장 확실)
            for (int d = 0; d < searchDirs.size() && f == null; d++) {
                f = findFileByName((File) searchDirs.get(d), fileName, 0);
            }
            if (f != null) matchType = "이름검색";

            // ★ 전략 2: 전체 상대경로 매칭
            if (f == null) {
                for (int d = 0; d < searchDirs.size() && f == null; d++) {
                    File candidate = new File((File) searchDirs.get(d),
                            path.replace('/', File.separatorChar));
                    if (candidate.isFile() && candidate.length() <= 200000) {
                        f = candidate;
                        matchType = "경로매칭";
                    }
                }
            }

            // ★ 전략 3: 접두사 붙여서 매칭 (src/main/java 등)
            if (f == null) {
                String trimPath = path;
                while (f == null && trimPath.contains("/")) {
                    trimPath = trimPath.substring(trimPath.indexOf('/') + 1);
                    String[] prefixes = {"src/main/java/", "src/main/resources/",
                            "src/main/webapp/", "src/"};
                    for (int d = 0; d < searchDirs.size() && f == null; d++) {
                        for (int p = 0; p < prefixes.length && f == null; p++) {
                            File candidate = new File((File) searchDirs.get(d),
                                    (prefixes[p] + trimPath).replace('/', File.separatorChar));
                            if (candidate.isFile() && candidate.length() <= 200000) {
                                f = candidate;
                                matchType = "접두사매칭";
                            }
                        }
                    }
                }
            }

            // 결과 기록
            if (f != null) {
                String content = readFileContent(f, 200000);
                if (content.length() > 0) {
                    if (!first) sb.append(",");
                    first = false;
                    sb.append("\"").append(NoriApiClient.escapeJson(path)).append("\":\"")
                      .append(NoriApiClient.escapeJson(content)).append("\"");
                    count++;
                    debugLog.append(" ✓").append(fileName).append("(").append(matchType).append(")");
                } else {
                    debugLog.append(" ✗").append(fileName).append("(빈파일)");
                }
            } else {
                debugLog.append(" ✗").append(fileName).append("(못찾음)");
                System.out.println("[NORI-FILE] 못 찾음: " + path);
            }
        }
        sb.append("}");

        // ── 3. UI에 결과 표시 ──
        String summary = count + "/" + filePaths.size() + "개 파일 읽음"
                + " [" + dirNames + "]" + debugLog;
        if (count < filePaths.size()) {
            addStepOnUI("step-file-detail", summary);
        }

        System.out.println("[NORI-FILE] " + summary + " → " + sb.length() + "B");
        return sb.toString();
    }

    /** 디렉토리에서 파일명으로 재귀 검색 (대소문자 무시 + 확장 탐색) */
    private File findFileByName(File dir, String fileName, int depth) {
        if (depth > 15 || dir == null || !dir.isDirectory()) return null;
        File[] children = dir.listFiles();
        if (children == null) return null;
        String fileNameLower = fileName.toLowerCase();
        // 파일 먼저 확인 (exact match 우선, 없으면 case-insensitive)
        File caseInsensitiveMatch = null;
        for (int i = 0; i < children.length; i++) {
            if (children[i].isFile() && children[i].length() <= 200000) {
                if (children[i].getName().equals(fileName)) {
                    return children[i];  // exact match
                }
                if (caseInsensitiveMatch == null
                        && children[i].getName().toLowerCase().equals(fileNameLower)) {
                    caseInsensitiveMatch = children[i];
                }
            }
        }
        if (caseInsensitiveMatch != null) return caseInsensitiveMatch;
        // 하위 디렉토리 탐색
        for (int i = 0; i < children.length; i++) {
            if (children[i].isDirectory()) {
                String name = children[i].getName();
                if (name.startsWith(".") || "target".equals(name) || "build".equals(name)
                        || "node_modules".equals(name) || "bin".equals(name)
                        || "test-output".equals(name) || "classes".equals(name)
                        || "generated-sources".equals(name)) continue;
                File found = findFileByName(children[i], fileName, depth + 1);
                if (found != null) return found;
            }
        }
        return null;
    }

    /* ═══════════════════════════════════════════════════════
     *  에디터 블록 선택 가져오기
     * ═══════════════════════════════════════════════════════ */

    private String getSelectedCode() {
        try {
            IWorkbenchPage page = PlatformUI.getWorkbench()
                    .getActiveWorkbenchWindow().getActivePage();
            if (page == null) return null;
            IEditorPart editor = page.getActiveEditor();
            if (editor == null) return null;

            ITextEditor textEditor = null;
            if (editor instanceof ITextEditor) {
                textEditor = (ITextEditor) editor;
            } else {
                Object adapted = editor.getAdapter(ITextEditor.class);
                if (adapted instanceof ITextEditor) {
                    textEditor = (ITextEditor) adapted;
                }
            }
            if (textEditor == null) return null;

            // 1) 블록 선택된 텍스트가 있으면 그것을 반환
            ISelection sel = textEditor.getSelectionProvider().getSelection();
            if (sel instanceof ITextSelection) {
                String text = ((ITextSelection) sel).getText();
                if (text != null && text.trim().length() > 0) return text;
            }

            // 2) 블록 선택 없으면 현재 열린 파일 전체 내용을 반환 (최대 30000자)
            IDocument doc = textEditor.getDocumentProvider()
                    .getDocument(textEditor.getEditorInput());
            if (doc != null) {
                String fullText = doc.get();
                if (fullText != null && fullText.trim().length() > 0) {
                    if (fullText.length() > 30000) {
                        fullText = fullText.substring(0, 30000) + "\n...(이하 생략)";
                    }
                    return fullText;
                }
            }
        } catch (Exception e) { /* ignore */ }
        return null;
    }

    /** 현재 에디터에서 열린 파일의 이름을 반환 */
    private String getActiveEditorFileName() {
        try {
            IWorkbenchPage page = PlatformUI.getWorkbench()
                    .getActiveWorkbenchWindow().getActivePage();
            if (page == null) return null;
            IEditorPart editor = page.getActiveEditor();
            if (editor == null) return null;
            if (editor.getEditorInput() instanceof IFileEditorInput) {
                return ((IFileEditorInput) editor.getEditorInput())
                        .getFile().getName();
            }
            return editor.getTitle();
        } catch (Exception e) { return null; }
    }

    /* ═══════════════════════════════════════════════════════
     *  프로젝트 프로파일 — .nori-profile.md 기반 컨텍스트
     * ═══════════════════════════════════════════════════════ */

    /** 프로젝트 프로파일 재생성 (프로젝트 분석 시 호출) */
    public void refreshProfile(final File projectDir) {
        if (projectDir == null || !projectDir.isDirectory()) return;
        String profile = generateProjectProfile(projectDir);
        if (profile.length() > 0) {
            profile = attachSummary(profile);
            saveTextFile(new File(projectDir, PROFILE_FILENAME), profile);
        }
    }

    /** AI 기반 프로젝트 분석 시작 (배너 버튼에서 호출) */
    private void startAIProjectAnalysis(final boolean refreshMode) {
        final File projectDir = getActiveProjectDir();
        if (projectDir == null || !projectDir.isDirectory()) {
            messages.add(new String[]{"system",
                "\u26A0 프로젝트를 찾을 수 없습니다. 에디터에서 파일을 하나 열어주세요.", null});
            refreshDisplay();
            return;
        }
        profileState = 1; // 분석중
        profileAnalysisStartTime = System.currentTimeMillis();
        if (refreshMode) {
            messages.add(new String[]{"system",
                "\uD83D\uDD04 프로젝트 갱신 시작... (기존 설명은 유지하고 추가/변경된 파일만 AI 분석)", null});
        } else {
            messages.add(new String[]{"system",
                "\uD83D\uDE80 프로젝트 전체를 AI로 분석합니다.\n"
                + "모든 소스코드를 AI에 보내 정확한 설명을 생성합니다.\n"
                + "\u23F3 분석이 완료될 때까지 응답이 느려질 수 있으니 잠시 기다려주세요.", null});
        }
        refreshDisplay();

        Job job = new Job("Nori AI - 프로젝트 AI 분석") {
            protected IStatus run(IProgressMonitor monitor) {
                try {
                    generateAIProjectProfile(projectDir, refreshMode);
                } catch (Exception e) {
                    addSystemMessageOnUI("\u274C 분석 중 오류: " + e.getMessage());
                    profileState = 0;
                    autoAnalysisTriggered = false;
                }
                return Status.OK_STATUS;
            }
        };
        job.setUser(false);
        job.schedule();
    }

    /** 우클릭 메뉴: 현재 파일의 AI 설명을 프로필에 업데이트 */
    public void updateFileProfile(final File projectDir, final File targetFile) {
        final String fileName = targetFile.getName();
        messages.add(new String[]{"system",
            "\uD83D\uDD04 " + fileName + " AI 분석 업데이트 중...", null});
        refreshDisplay();

        Job job = new Job("Nori AI - \ud30c\uc77c \ubd84\uc11d") {
            protected IStatus run(IProgressMonitor monitor) {
                try {
                    NoriApiClient api = NoriApiClient.getInstance();
                    String content = readFileContent(targetFile, 200000);
                    if (content.length() == 0) {
                        addSystemMessageOnUI("\u26A0 파일 내용을 읽을 수 없습니다: " + fileName);
                        return Status.OK_STATUS;
                    }

                    // AI 설명 생성
                    String aiDesc = api.describeFile(content, fileName);
                    if (aiDesc == null || aiDesc.length() == 0
                            || aiDesc.startsWith("\uc5d0\ub7ec") || aiDesc.startsWith("\uc11c\ubc84")) {
                        addSystemMessageOnUI("\u274C AI 설명 생성에 실패했습니다: " + fileName);
                        return Status.OK_STATUS;
                    }

                    // 프로필 파일 업데이트
                    File profileFile = new File(projectDir, PROFILE_FILENAME);
                    if (!profileFile.exists()) {
                        addSystemMessageOnUI("\u26A0 프로필이 없습니다. 먼저 프로젝트 분석을 실행해주세요.");
                        return Status.OK_STATUS;
                    }

                    String profile = readFileContent(profileFile, 200000);
                    String marker = "### " + fileName;
                    int pos = profile.indexOf(marker);

                    if (pos >= 0) {
                        // 기존 설명 교체
                        int descStart = profile.indexOf("\n", pos);
                        if (descStart < 0) descStart = profile.length();
                        // 다음 ### 또는 ## 찾기
                        int nextSection = profile.indexOf("\n### ", descStart + 1);
                        int nextHeader = profile.indexOf("\n## ", descStart + 1);
                        int endPos = profile.length();
                        if (nextSection >= 0) endPos = Math.min(endPos, nextSection);
                        if (nextHeader >= 0) endPos = Math.min(endPos, nextHeader);

                        // 새 블록 구성
                        StringBuilder newBlock = new StringBuilder();
                        newBlock.append("\n");

                        // 경로 유지
                        String relPath = relativePath(targetFile, projectDir);
                        newBlock.append("\uacbd\ub85c: ").append(relPath).append("\n");

                        // AI설명/설명 라벨 선택
                        if (fileName.endsWith(".xml")) {
                            newBlock.append("AI\uc124\uba85: ").append(aiDesc.trim()).append("\n");
                        } else {
                            newBlock.append("\uc124\uba85: ").append(aiDesc.trim()).append("\n");
                        }

                        // Java면 메서드/어노테이션 정보도 재생성
                        if (fileName.endsWith(".java")) {
                            appendJavaMethodInfo(newBlock, content);
                        }
                        newBlock.append("\n");

                        profile = profile.substring(0, descStart) + newBlock.toString()
                                + profile.substring(endPos);
                    } else {
                        // 프로필에 없는 파일 — 적절한 섹션 맨 끝에 추가
                        String insertSection = findInsertSection(fileName);
                        int sectionPos = profile.indexOf(insertSection);
                        if (sectionPos < 0) {
                            // 적절한 섹션 없으면 맨 뒤에 추가
                            sectionPos = profile.length();
                        } else {
                            // 해당 섹션의 다음 ## 앞에 삽입
                            int afterSection = profile.indexOf("\n## ", sectionPos + insertSection.length());
                            sectionPos = afterSection >= 0 ? afterSection : profile.length();
                        }

                        StringBuilder newEntry = new StringBuilder();
                        newEntry.append("\n### ").append(fileName).append("\n");
                        newEntry.append("\uacbd\ub85c: ").append(relativePath(targetFile, projectDir)).append("\n");
                        if (fileName.endsWith(".xml")) {
                            newEntry.append("AI\uc124\uba85: ").append(aiDesc.trim()).append("\n");
                        } else {
                            newEntry.append("\uc124\uba85: ").append(aiDesc.trim()).append("\n");
                        }
                        if (fileName.endsWith(".java")) {
                            appendJavaMethodInfo(newEntry, content);
                        }
                        newEntry.append("\n");

                        profile = profile.substring(0, sectionPos) + newEntry.toString()
                                + profile.substring(sectionPos);
                    }

                    saveTextFile(profileFile, profile);
                    addSystemMessageOnUI("\u2705 " + fileName + " AI 분석이 프로필에 업데이트되었습니다.\n설명: " + aiDesc.trim());
                } catch (Exception e) {
                    addSystemMessageOnUI("\u274C 분석 중 오류: " + e.getMessage());
                }
                return Status.OK_STATUS;
            }
        };
        job.setUser(false);
        job.schedule();
    }

    /** 파일명으로 프로필 섹션 헤더 결정 */
    private String findInsertSection(String fileName) {
        String lower = fileName.toLowerCase();
        if (lower.endsWith("controller.java") || lower.endsWith("action.java"))
            return "## Controller";
        if (lower.endsWith("service.java") || lower.endsWith("serviceimpl.java"))
            return "## Service";
        if (lower.endsWith("dao.java") || lower.endsWith("mapper.java") || lower.endsWith("repository.java"))
            return "## DAO";
        if (lower.endsWith("vo.java") || lower.endsWith("dto.java") || lower.endsWith("entity.java"))
            return "## VO/DTO";
        if (lower.endsWith(".xml"))
            return "## MyBatis Mapper";
        if (lower.endsWith(".jsp"))
            return "## JSP";
        if (lower.endsWith(".css"))
            return "## CSS";
        if (lower.endsWith(".js"))
            return "## JavaScript";
        return "## \uae30\ud0c0 Java";
    }

    /** Java 소스에서 메서드/어노테이션 정보 추출하여 StringBuilder에 추가 */
    private void appendJavaMethodInfo(StringBuilder sb, String content) {
        String[] lines = content.split("\n");
        String pendingAnnotation = "";
        boolean inBlockComment = false;
        for (int j = 0; j < lines.length; j++) {
            String line = lines[j].trim();
            if (line.startsWith("/*")) inBlockComment = true;
            if (line.contains("*/")) { inBlockComment = false; continue; }
            if (inBlockComment || line.startsWith("//") || line.startsWith("*")) continue;
            if (line.startsWith("package ")) {
                sb.append(line).append("\n");
                continue;
            }
            if (line.startsWith("@")) {
                if (isImportantAnnotation(line)) pendingAnnotation = line;
                continue;
            }
            if (isClassDeclaration(line)) {
                if (pendingAnnotation.length() > 0) {
                    sb.append(pendingAnnotation).append("\n");
                    pendingAnnotation = "";
                }
                int braceIdx = line.indexOf('{');
                sb.append(braceIdx >= 0 ? line.substring(0, braceIdx).trim() : line).append("\n");
                continue;
            }
            if (isMethodSignature(line)) {
                if (pendingAnnotation.length() > 0) {
                    sb.append("  ").append(pendingAnnotation).append("\n");
                    pendingAnnotation = "";
                }
                int braceIdx = line.indexOf('{');
                sb.append("  ").append(braceIdx >= 0 ? line.substring(0, braceIdx).trim() : line).append("\n");
            }
        }
    }

    /** AI 기반 프로필 생성: 각 Java 파일을 서버에 보내 설명을 받아 프로필 구성 */
    private void generateAIProjectProfile(File projectDir, boolean refreshMode) {
        final NoriApiClient api = NoriApiClient.getInstance();
        stopRequested = false;

        // 갱신 모드: 기존 프로필에서 이미 설명된 파일들의 설명을 파싱
        java.util.Map existingDescs = new java.util.HashMap();
        if (refreshMode) {
            File profileFile = new File(projectDir, PROFILE_FILENAME);
            if (profileFile.exists()) {
                String existing = readFileContent(profileFile, 100000);
                parseExistingDescriptions(existing, existingDescs);
            }
        }

        // ── 멀티 프로젝트: 워크스페이스 전체 프로젝트 및 서버 설정 수집 ──
        java.util.List allProjectDirs = getAllWorkspaceProjectDirs();
        String[] serverSettings = findServerSettings();
        String serverXmlContent = serverSettings[0];
        String contextXmlContent = serverSettings[1];
        String workspaceTree = buildWorkspaceTree();

        // 분석 대상 파일 수집 (현재 프로젝트 기준 — 멀티프로젝트 정보는 프로필 상단에 포함)
        List javaFiles = new ArrayList();
        findAllFiles(projectDir, ".java", javaFiles, 0, 200);

        List mapperXmls = new ArrayList();
        findAllFiles(projectDir, "Mapper.xml", mapperXmls, 0, 50);
        findAllFiles(projectDir, "_SQL.xml", mapperXmls, 0, 50);
        findAllFiles(projectDir, "_sql.xml", mapperXmls, 0, 50);
        List allXmls = new ArrayList();
        findAllFiles(projectDir, ".xml", allXmls, 0, 200);
        for (int i = 0; i < allXmls.size(); i++) {
            File xf = (File) allXmls.get(i);
            String xfName = xf.getName().toLowerCase();
            if ((xfName.startsWith("mybatis") || xfName.contains("_sql_"))
                    && !mapperXmls.contains(xf)) {
                mapperXmls.add(xf);
            }
        }

        List jspFiles = new ArrayList();
        findAllFiles(projectDir, ".jsp", jspFiles, 0, 100);

        List cssFiles = new ArrayList();
        findAllFiles(projectDir, ".css", cssFiles, 0, 80);

        List jsFiles = new ArrayList();
        findAllFiles(projectDir, ".js", jsFiles, 0, 80);
        // node_modules, min.js 등 라이브러리 파일 제외
        for (int i = jsFiles.size() - 1; i >= 0; i--) {
            File jf = (File) jsFiles.get(i);
            String path = jf.getAbsolutePath().replace('\\', '/');
            String name = jf.getName().toLowerCase();
            if (path.contains("/node_modules/") || path.contains("/vendor/")
                    || path.contains("/lib/") || path.contains("/libs/")
                    || name.endsWith(".min.js") || name.startsWith("jquery")
                    || name.startsWith("bootstrap") || name.startsWith("vue.")
                    || name.startsWith("react.")) {
                jsFiles.remove(i);
            }
        }
        // CSS도 라이브러리 제외
        for (int i = cssFiles.size() - 1; i >= 0; i--) {
            File cf = (File) cssFiles.get(i);
            String path = cf.getAbsolutePath().replace('\\', '/');
            String name = cf.getName().toLowerCase();
            if (path.contains("/node_modules/") || path.contains("/vendor/")
                    || path.contains("/lib/") || path.contains("/libs/")
                    || name.endsWith(".min.css") || name.startsWith("bootstrap")
                    || name.startsWith("tailwind")) {
                cssFiles.remove(i);
            }
        }

        int totalFiles = javaFiles.size() + mapperXmls.size() + jspFiles.size()
                + cssFiles.size() + jsFiles.size();
        // others가 30개 초과 시 AI 분석하지 않으므로 totalFiles에서 제외
        int skippedOthers = 0;
        addSystemMessageOnUI("\uD83D\uDD0D 분석 대상: Java " + javaFiles.size()
                + "개, XML " + mapperXmls.size()
                + "개, JSP " + jspFiles.size()
                + "개, CSS " + cssFiles.size()
                + "개, JS " + jsFiles.size() + "개 (총 " + totalFiles + "개)");

        // 기본 프로필 생성
        StringBuilder sb = new StringBuilder();
        sb.append("# 프로젝트 프로파일: ").append(projectDir.getName()).append("\n\n");

        // 0) 워크스페이스 멀티 프로젝트 구조
        if (allProjectDirs.size() > 1) {
            sb.append("## \uD83D\uDCC2 워크스페이스 프로젝트 구조\n");
            sb.append("이 워크스페이스에는 ").append(allProjectDirs.size()).append("개의 프로젝트가 있습니다.\n\n");
            for (int p = 0; p < allProjectDirs.size(); p++) {
                File pd = (File) allProjectDirs.get(p);
                sb.append("- **").append(pd.getName()).append("**");
                if (pd.getAbsolutePath().equals(projectDir.getAbsolutePath())) {
                    sb.append(" ← 현재 분석 대상");
                }
                sb.append("\n");
            }
            sb.append("\n```\n").append(workspaceTree.length() > 10000 ? workspaceTree.substring(0, 10000) + "\n...(\uc774\ud558 \uc0dd\ub7b5)\n" : workspaceTree).append("```\n\n");
        }

        // 0-1) 서버 설정 (server.xml / context.xml)
        if (serverXmlContent.length() > 0 || contextXmlContent.length() > 0) {
            sb.append("## \uD83D\uDE80 서버 배포 설정\n\n");
            if (serverXmlContent.length() > 0) {
                sb.append("### server.xml\n```xml\n").append(serverXmlContent).append("\n```\n\n");
            }
            if (contextXmlContent.length() > 0) {
                sb.append("### context.xml\n```xml\n").append(contextXmlContent).append("\n```\n\n");
            }
        }

        // 1) 파일 구조
        sb.append("## 파일 구조\n```\n");
        String tree = buildProfileTree(projectDir, "", 0);
        if (tree.length() > 15000) tree = tree.substring(0, 15000) + "\n...(이하 생략)\n";
        sb.append(tree).append("```\n\n");

        // 2) 설정 파일
        sb.append("## 설정 파일\n\n");
        appendConfigSection(sb, projectDir, "pom.xml", 5000);
        appendConfigSection(sb, projectDir, "build.gradle", 3000);
        File webXml = findFileDeep(projectDir, "web.xml", 5);
        if (webXml != null) {
            sb.append("### web.xml\n```xml\n").append(readFileContent(webXml, 3000)).append("\n```\n\n");
        }
        File appProps = findFileDeep(projectDir, "application.properties", 5);
        if (appProps != null) {
            sb.append("### application.properties\n```\n").append(readFileContent(appProps, 2000)).append("\n```\n\n");
        } else {
            File appYml = findFileDeep(projectDir, "application.yml", 5);
            if (appYml != null) {
                sb.append("### application.yml\n```yaml\n").append(readFileContent(appYml, 2000)).append("\n```\n\n");
            }
        }

        // 2-1) 인프라 설정 (혈관 로직) 수집
        appendInfrastructureSection(sb, allProjectDirs);

        // 3) Java 클래스 AI 분석
        List controllers = new ArrayList();
        List services = new ArrayList();
        List daos = new ArrayList();
        List vos = new ArrayList();
        List others = new ArrayList();

        for (int i = 0; i < javaFiles.size(); i++) {
            File f = (File) javaFiles.get(i);
            String name = f.getName();
            if (name.endsWith("Controller.java") || name.endsWith("Action.java")) {
                controllers.add(f);
            } else if (name.endsWith("Service.java") || name.endsWith("ServiceImpl.java")) {
                services.add(f);
            } else if (name.endsWith("Dao.java") || name.endsWith("DAO.java")
                    || name.endsWith("Mapper.java") || name.endsWith("Repository.java")) {
                daos.add(f);
            } else if (name.endsWith("Vo.java") || name.endsWith("VO.java")
                    || name.endsWith("Dto.java") || name.endsWith("DTO.java")
                    || name.endsWith("Entity.java")) {
                vos.add(f);
            } else {
                others.add(f);
            }
        }

        int done = 0;
        done = appendAIJavaCategory(sb, "Controller", controllers, projectDir, api, done, totalFiles, existingDescs);
        if (stopRequested) { finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, true); return; }
        done = appendAIJavaCategory(sb, "Service", services, projectDir, api, done, totalFiles, existingDescs);
        if (stopRequested) { finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, true); return; }
        done = appendAIJavaCategory(sb, "DAO", daos, projectDir, api, done, totalFiles, existingDescs);
        if (stopRequested) { finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, true); return; }
        done = appendAIJavaCategory(sb, "VO/DTO", vos, projectDir, api, done, totalFiles, existingDescs);
        if (stopRequested) { finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, true); return; }
        if (others.size() > 0 && others.size() <= 30) {
            done = appendAIJavaCategory(sb, "기타 Java", others, projectDir, api, done, totalFiles, existingDescs);
            if (stopRequested) { finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, true); return; }
        } else if (others.size() > 30) {
            skippedOthers = others.size();
            totalFiles -= skippedOthers;
        }

        // 4) MyBatis Mapper XML — AI 설명 포함
        if (mapperXmls.size() > 0) {
            sb.append("## MyBatis Mapper (").append(mapperXmls.size()).append("개)\n\n");
            for (int i = 0; i < mapperXmls.size(); i++) {
                if (stopRequested) { finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, true); return; }
                File xf = (File) mapperXmls.get(i);
                appendMapperInfo(sb, xf, projectDir);

                String existDesc = (String) existingDescs.get(xf.getName());
                if (existDesc != null && existDesc.length() > 0) {
                    sb.append("AI설명: ").append(existDesc).append("\n");
                } else {
                    String xmlContent = readFileContent(xf, 200000);
                    if (xmlContent.length() > 0) {
                        try {
                            String aiDesc = api.describeFile(xmlContent, xf.getName());
                            if (!isApiErrorResponse(aiDesc)) {
                                sb.append("AI설명: ").append(aiDesc.trim()).append("\n");
                            }
                        } catch (Exception e) { /* fallback: 기존 파싱만 사용 */ }
                    }
                }

                done++;
                updateProgressOnUI(done, totalFiles, xf.getName());
            }
            sb.append("\n");
        }

        // 5) JSP — AI 설명 포함
        if (jspFiles.size() > 0) {
            sb.append("## JSP 페이지 (").append(jspFiles.size()).append("개)\n\n");
            for (int i = 0; i < jspFiles.size(); i++) {
                if (stopRequested) { finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, true); return; }
                File jf = (File) jspFiles.get(i);
                sb.append("### ").append(jf.getName()).append("\n");
                sb.append("경로: ").append(relativePath(jf, projectDir)).append("\n");

                String existDesc = (String) existingDescs.get(jf.getName());
                if (existDesc != null && existDesc.length() > 0) {
                    sb.append("설명: ").append(existDesc).append("\n");
                } else {
                    String jspContent = readFileContent(jf, 200000);
                    if (jspContent.length() > 0) {
                        try {
                            String aiDesc = api.describeFile(jspContent, jf.getName());
                            if (!isApiErrorResponse(aiDesc)) {
                                sb.append("설명: ").append(aiDesc.trim()).append("\n");
                            }
                        } catch (Exception e) { /* skip */ }
                    }
                }
                sb.append("\n");

                done++;
                updateProgressOnUI(done, totalFiles, jf.getName());
            }
        }

        // 6) CSS — AI 설명 포함
        if (cssFiles.size() > 0) {
            sb.append("## CSS (" ).append(cssFiles.size()).append("개)\n\n");
            for (int i = 0; i < cssFiles.size(); i++) {
                if (stopRequested) { finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, true); return; }
                File cf = (File) cssFiles.get(i);
                sb.append("### ").append(cf.getName()).append("\n");
                sb.append("경로: ").append(relativePath(cf, projectDir)).append("\n");

                String existDesc = (String) existingDescs.get(cf.getName());
                if (existDesc != null && existDesc.length() > 0) {
                    sb.append("설명: ").append(existDesc).append("\n");
                } else {
                    String cssContent = readFileContent(cf, 200000);
                    if (cssContent.length() > 0) {
                        try {
                            String aiDesc = api.describeFile(cssContent, cf.getName());
                            if (!isApiErrorResponse(aiDesc)) {
                                sb.append("설명: ").append(aiDesc.trim()).append("\n");
                            }
                        } catch (Exception e) { /* skip */ }
                    }
                }
                sb.append("\n");
                done++;
                updateProgressOnUI(done, totalFiles, cf.getName());
            }
        }

        // 7) JavaScript — AI 설명 포함
        if (jsFiles.size() > 0) {
            sb.append("## JavaScript (" ).append(jsFiles.size()).append("개)\n\n");
            for (int i = 0; i < jsFiles.size(); i++) {
                if (stopRequested) { finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, true); return; }
                File jf = (File) jsFiles.get(i);
                sb.append("### ").append(jf.getName()).append("\n");
                sb.append("경로: ").append(relativePath(jf, projectDir)).append("\n");

                String existDesc = (String) existingDescs.get(jf.getName());
                if (existDesc != null && existDesc.length() > 0) {
                    sb.append("설명: ").append(existDesc).append("\n");
                } else {
                    String jsContent = readFileContent(jf, 200000);
                    if (jsContent.length() > 0) {
                        try {
                            String aiDesc = api.describeFile(jsContent, jf.getName());
                            if (!isApiErrorResponse(aiDesc)) {
                                sb.append("설명: ").append(aiDesc.trim()).append("\n");
                            }
                        } catch (Exception e) { /* skip */ }
                    }
                }
                sb.append("\n");
                done++;
                updateProgressOnUI(done, totalFiles, jf.getName());
            }
        }

        // 8) SQL 파일 목록
        List sqlFiles = new ArrayList();
        findAllFiles(projectDir, ".sql", sqlFiles, 0, 20);
        if (sqlFiles.size() > 0) {
            sb.append("## SQL 파일\n\n");
            for (int i = 0; i < sqlFiles.size(); i++) {
                sb.append("- ").append(relativePath((File) sqlFiles.get(i), projectDir)).append("\n");
            }
            sb.append("\n");
        }

        finishProfileSave(sb, projectDir, existingDescs, totalFiles, refreshMode, false);
    }

    /** 프로필 저장 완료 처리 */
    private void finishProfileSave(StringBuilder sb, File projectDir,
                                    java.util.Map existingDescs, int totalFiles,
                                    boolean refreshMode, boolean stopped) {
        try {
            if (stopped) {
                addSystemMessageOnUI("\u26A0 분석이 중단되었습니다. 현재까지의 결과를 저장합니다.");
            } else {
                addSystemMessageOnUI("\uD83D\uDCDD AI 요약 생성 중...");
            }
            String profile = sb.toString();
            if (!stopped) {
                profile = attachSummary(profile);
            }
            boolean saved = saveTextFile(new File(projectDir, PROFILE_FILENAME), profile);
            if (!saved) {
                addSystemMessageOnUI("\u274C 프로필 파일 저장 실패! 경로: " + projectDir.getAbsolutePath());
            }

            profileState = 2;
            try {
                Display.getDefault().asyncExec(new Runnable() {
                    public void run() {
                        if (projectCheck != null && !projectCheck.isDisposed()) {
                            projectCheck.setEnabled(true);
                            projectCheck.setSelection(true);
                            projectCheck.setToolTipText("\ud604\uc7ac \ud504\ub85c\uc81d\ud2b8 \uc18c\uc2a4 \ucf54\ub4dc\ub97c AI\uc5d0 \uc804\ub2ec");
                        }
                        if (statusLabel != null && !statusLabel.isDisposed()) {
                            String url = NoriApiClient.getInstance().getServerUrl();
                            statusLabel.setText("\u25CF \uc5f0\uacb0\ub428 \u2014 " + url);
                        }
                    }
                });
            } catch (Exception uiEx) { /* Display가 이미 disposed */ }

            int reused = existingDescs.size();
            if (stopped) {
                addSystemMessageOnUI("\u2705 중단된 분석 결과 저장 완료. 나머지는 갱신 버튼으로 이어서 분석하세요.");
            } else if (refreshMode && reused > 0) {
                addSystemMessageOnUI("\u2705 프로젝트 갱신 완료! (기존 " + reused + "개 유지, 신규 " + (totalFiles - reused) + "개 AI 분석)");
            } else {
                addSystemMessageOnUI("\u2705 프로젝트 AI 분석 완료! 이제 프로젝트에 대한 질문에 정확한 답변을 받을 수 있습니다.");
            }
            uploadProfileToServer(profile, projectDir);
        } catch (Exception e) {
            System.err.println("[Nori] finishProfileSave 예외: " + e.getMessage());
            profileState = 0;
            autoAnalysisTriggered = false;
            addSystemMessageOnUI("\u274C 프로필 저장 중 오류: " + e.getMessage());
        }
    }

    private void uploadProfileToServer(final String profileContent, final File projectDir) {
        final NoriApiClient api = NoriApiClient.getInstance();
        String baseUrl = api.getServerUrl();
        if (baseUrl == null || baseUrl.trim().isEmpty()) return;

        // 서버 설정 및 워크스페이스 트리 수집 (UI 스레드에서 안전하게)
        final String[] serverSettings = findServerSettings();
        final String wsTree = buildWorkspaceTree();

        new Thread(new Runnable() {
            public void run() {
                try {
                    java.util.List sourceFiles = collectSourceFilesForUpload(projectDir);
                    String projectId = projectDir != null ? projectDir.getName() : "";
                    String name = projectId;
                    String result = api.uploadProfile(profileContent, projectId, name, sourceFiles,
                            serverSettings[0], serverSettings[1], wsTree);
                    if (result != null && !result.startsWith("\uc11c\ubc84") && !result.startsWith("\uc5d0\ub7ec")
                            && !result.startsWith("\uc624\ub958") && !result.contains("\uc5f0\uacb0")) {
                        addSystemMessageOnUI("\ud83d\ude80 프로필이 서버에 업로드되었습니다. (project=" + result + ")");
                    }
                } catch (Exception e) {
                    addSystemMessageOnUI("\u26a0\ufe0f 프로필 서버 업로드 실패: " + e.getMessage());
                }
            }
        }, "Nori-ProfileUpload").start();
    }

    /** 프로그레스 업데이트 (퍼센트 + 파일명) */
    private void updateProgressOnUI(final int done, final int total, final String filename) {
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                if (statusLabel != null && !statusLabel.isDisposed()) {
                    int pct = total > 0 ? (done * 100 / total) : 0;
                    statusLabel.setText("\uD83D\uDD0D [" + pct + "%] " + done + "/" + total + " \u2014 " + filename);
                }
            }
        });
    }

    /** 기존 프로필에서 파일명 → 설명 매핑을 파싱 */
    private void parseExistingDescriptions(String profile, java.util.Map map) {
        String[] lines = profile.split("\n");
        String currentFile = null;
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            if (line.startsWith("### ") && (line.endsWith(".java") || line.endsWith(".xml")
                    || line.endsWith(".jsp"))) {
                currentFile = line.substring(4).trim();
            } else if (currentFile != null
                    && (line.startsWith("설명: ") || line.startsWith("AI설명: "))) {
                int colonIdx = line.indexOf(": ");
                if (colonIdx >= 0) {
                    map.put(currentFile, line.substring(colonIdx + 2).trim());
                }
                currentFile = null;
            } else if (line.startsWith("## ") || line.startsWith("# ")) {
                currentFile = null;
            }
        }
    }

    /** AI 기반 Java 카테고리 분석 - 각 파일을 서버에 보내 설명 생성 */
    private int appendAIJavaCategory(StringBuilder sb, String category, List files,
                                      File projectDir, NoriApiClient api,
                                      int doneCount, int totalCount,
                                      java.util.Map existingDescs) {
        if (files.isEmpty()) return doneCount;
        sb.append("## ").append(category).append(" (").append(files.size()).append("개)\n\n");
        for (int i = 0; i < files.size(); i++) {
            if (stopRequested) return doneCount;
            File f = (File) files.get(i);
            String content = readFileContent(f, 200000);
            if (content.length() == 0) continue;

            sb.append("### ").append(f.getName()).append("\n");
            sb.append("경로: ").append(relativePath(f, projectDir)).append("\n");

            // 기존 설명이 있으면 재사용 (갱신 모드)
            String existDesc = (String) existingDescs.get(f.getName());
            if (existDesc != null && existDesc.length() > 0) {
                sb.append("설명: ").append(existDesc).append("\n");
            } else {
                // AI 설명 생성
                String aiDesc = null;
                try {
                    aiDesc = api.describeFile(content, f.getName());
                } catch (Exception e) { /* AI 실패 시 키워드 기반 폴백 */ }

                if (!isApiErrorResponse(aiDesc)) {
                    sb.append("설명: ").append(aiDesc.trim()).append("\n");
                } else {
                    String fallbackDesc = guessClassDescription(f.getName(), content);
                    if (fallbackDesc.length() > 0) {
                        sb.append("설명: ").append(fallbackDesc).append("\n");
                    }
                }
            }

            // 메서드/어노테이션 정보 추출 (기존 extractJavaInfo 로직 활용)
            String[] lines = content.split("\n");
            String pendingAnnotation = "";
            boolean inBlockComment = false;

            for (int j = 0; j < lines.length; j++) {
                String line = lines[j].trim();
                if (line.startsWith("/*")) inBlockComment = true;
                if (line.contains("*/")) { inBlockComment = false; continue; }
                if (inBlockComment || line.startsWith("//") || line.startsWith("*")) continue;

                if (line.startsWith("package ")) {
                    sb.append(line).append("\n");
                    continue;
                }
                if (line.startsWith("@")) {
                    if (isImportantAnnotation(line)) pendingAnnotation = line;
                    continue;
                }
                if (isClassDeclaration(line)) {
                    if (pendingAnnotation.length() > 0) {
                        sb.append(pendingAnnotation).append("\n");
                        pendingAnnotation = "";
                    }
                    int braceIdx = line.indexOf('{');
                    sb.append(braceIdx >= 0 ? line.substring(0, braceIdx).trim() : line).append("\n");
                    continue;
                }
                if (isMethodSignature(line)) {
                    if (pendingAnnotation.length() > 0) {
                        sb.append("  ").append(pendingAnnotation).append("\n");
                        pendingAnnotation = "";
                    }
                    int braceIdx = line.indexOf('{');
                    sb.append("  ").append(braceIdx >= 0 ? line.substring(0, braceIdx).trim() : line).append("\n");
                }
            }
            sb.append("\n");

            doneCount++;
            updateProgressOnUI(doneCount, totalCount, f.getName());
        }
        return doneCount;
    }

    private void addSystemMessageOnUI(final String msg) {
        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                messages.add(new String[]{"system", msg, null});
                refreshDisplay();
            }
        });
    }

    private String collectProjectContext() {
        File projectDir = getActiveProjectDir();
        return collectProjectContextFromDir(projectDir);
    }

    private String collectProjectContextFromDir(File projectDir) {
        try {
            if (projectDir == null || !projectDir.isDirectory()) return "";

            File profileFile = new File(projectDir, PROFILE_FILENAME);
            if (profileFile.exists() && profileFile.length() > 100) {
                profileState = 2;
                return readFileContent(profileFile, 20000);
            }

            // 프로필 생성 (파일 스캔만 — LLM 호출 없이 즉시 완료)
            String profile = generateProjectProfile(projectDir);
            if (profile.length() > 0) {
                saveTextFile(profileFile, profile);
                profileState = 2;

                // LLM 요약은 별도 스레드에서 나중에 추가
                final File pf = profileFile;
                final String rawProfile = profile;
                new Thread(new Runnable() {
                    public void run() {
                        try {
                            String enriched = attachSummary(rawProfile);
                            if (enriched.length() > rawProfile.length()) {
                                saveTextFile(pf, enriched);
                            }
                        } catch (Exception ignored) { }
                    }
                }, "Nori-ProfileSummary").start();
            }
            return profile;
        } catch (Exception e) {
            return "";
        }
    }

    /** LLM에게 프로필을 분석시켜 비즈니스 기능 요약을 상단에 추가 */
    private String attachSummary(String rawProfile) {
        try {
            String summary = NoriApiClient.getInstance().summarizeProfile(rawProfile);
            if (summary != null && summary.trim().length() > 0) {
                return summary.trim() + "\n\n---\n\n" + rawProfile;
            }
        } catch (Exception e) { /* 요약 실패 시 원본 프로필 그대로 사용 */ }
        return rawProfile;
    }

    private File getActiveProjectDir() {
        // 1) 에디터 기반 프로젝트
        try {
            IWorkbenchPage page = PlatformUI.getWorkbench()
                    .getActiveWorkbenchWindow().getActivePage();
            if (page != null) {
                IEditorPart editor = page.getActiveEditor();
                if (editor != null && editor.getEditorInput() instanceof IFileEditorInput) {
                    IProject project = ((IFileEditorInput) editor.getEditorInput())
                            .getFile().getProject();
                    if (project != null && project.getLocation() != null) {
                        return project.getLocation().toFile();
                    }
                }
            }
        } catch (Exception e) { /* ignore */ }

        // 2) fallback: workspace 첫 번째 열린 프로젝트
        try {
            IProject[] projects = ResourcesPlugin.getWorkspace().getRoot().getProjects();
            for (int i = 0; i < projects.length; i++) {
                if (projects[i].isOpen() && projects[i].getLocation() != null) {
                    return projects[i].getLocation().toFile();
                }
            }
        } catch (Exception e) { /* ignore */ }
        return null;
    }

    /**
     * 워크스페이스 내 모든 열린 프로젝트 디렉토리 목록 반환.
     * Servers 프로젝트는 제외하고 별도 처리한다.
     */
    private java.util.List getAllWorkspaceProjectDirs() {
        java.util.List dirs = new ArrayList();
        try {
            IProject[] projects = ResourcesPlugin.getWorkspace().getRoot().getProjects();
            for (int i = 0; i < projects.length; i++) {
                if (projects[i].isOpen() && projects[i].getLocation() != null) {
                    String name = projects[i].getName();
                    // Servers 프로젝트는 별도 처리 대상이므로 제외
                    if ("Servers".equalsIgnoreCase(name)) continue;
                    dirs.add(projects[i].getLocation().toFile());
                }
            }
        } catch (Exception e) { /* ignore */ }
        return dirs;
    }

    /**
     * 워크스페이스 전체의 프로젝트 구조도(트리 문자열) 생성.
     */
    private String buildWorkspaceTree() {
        StringBuilder sb = new StringBuilder();
        java.util.List allDirs = getAllWorkspaceProjectDirs();
        for (int i = 0; i < allDirs.size(); i++) {
            File dir = (File) allDirs.get(i);
            sb.append("[").append(dir.getName()).append("]\n");
            String tree = buildProfileTree(dir, "  ", 0);
            if (tree.length() > 5000) tree = tree.substring(0, 5000) + "\n  ...(\uc774\ud558 \uc0dd\ub7b5)\n";
            sb.append(tree).append("\n");
        }
        if (sb.length() > 30000) {
            return sb.substring(0, 30000) + "\n...(\uc774\ud558 \uc0dd\ub7b5)\n";
        }
        return sb.toString();
    }

    /**
     * Eclipse Servers 프로젝트에서 server.xml, context.xml 등 서버 설정 텍스트를 추출.
     * 반환: [0]=server.xml 내용, [1]=context.xml 내용 (없으면 빈 문자열)
     */
    private String[] findServerSettings() {
        String serverXml = "";
        String contextXml = "";
        try {
            IProject[] projects = ResourcesPlugin.getWorkspace().getRoot().getProjects();
            for (int i = 0; i < projects.length; i++) {
                if (!projects[i].isOpen() || projects[i].getLocation() == null) continue;
                String name = projects[i].getName();
                // Servers 프로젝트이거나 이름에 "server"가 포함된 프로젝트
                if ("Servers".equalsIgnoreCase(name) || name.toLowerCase().contains("server")) {
                    File serverDir = projects[i].getLocation().toFile();
                    // 하위 폴더들을 탐색 (Tomcat, JBoss 등 서버 폴더)
                    File[] children = serverDir.listFiles();
                    if (children == null) continue;
                    for (int j = 0; j < children.length; j++) {
                        if (!children[j].isDirectory()) {
                            // 직접 server.xml이 있는 경우
                            if ("server.xml".equals(children[j].getName()) && serverXml.length() == 0) {
                                serverXml = readFileContent(children[j], 10000);
                            }
                            if ("context.xml".equals(children[j].getName()) && contextXml.length() == 0) {
                                contextXml = readFileContent(children[j], 5000);
                            }
                            continue;
                        }
                        // 서버 인스턴스 폴더 내부 탐색
                        File sxml = new File(children[j], "server.xml");
                        if (sxml.exists() && serverXml.length() == 0) {
                            serverXml = readFileContent(sxml, 10000);
                        }
                        File cxml = new File(children[j], "context.xml");
                        if (cxml.exists() && contextXml.length() == 0) {
                            contextXml = readFileContent(cxml, 5000);
                        }
                    }
                }
            }
        } catch (Exception e) { /* ignore */ }
        return new String[] { serverXml, contextXml };
    }

    /**
     * 인프라 설정 파일(혈관 로직) 수집 — 인증/인가, 예외 처리, 트랜잭션, 로깅, 외부 연동 설정을 프로필에 추가.
     * @param sb 프로필 StringBuilder
     * @param scanDirs 스캔 대상 프로젝트 디렉토리 목록
     */
    private void appendInfrastructureSection(StringBuilder sb, java.util.List scanDirs) {
        sb.append("## \uD83C\uDFE5 인프라 설정 (혈관 로직)\n\n");

        // 대상 파일 패턴 목록
        String[][] infraTargets = {
            // {파일명 패턴, 섹션 제목, 최대 길이}
            {"egov-security.xml",      "\uD83D\uDD10 인증/인가 — egov-security.xml",    "5000"},
            {"security-context.xml",   "\uD83D\uDD10 인증/인가 — security-context.xml", "5000"},
            {"LoginInterceptor.java",  "\uD83D\uDD10 인증/인가 — LoginInterceptor",     "8000"},
            {"AuthInterceptor.java",   "\uD83D\uDD10 인증/인가 — AuthInterceptor",      "8000"},
            {"context-transaction.xml","\uD83D\uDD04 트랜잭션 — context-transaction.xml","3000"},
            {"context-aspect.xml",     "\uD83D\uDD04 AOP — context-aspect.xml",         "3000"},
            {"log4j2.xml",             "\uD83D\uDCDD 로깅 — log4j2.xml",                "3000"},
            {"logback.xml",            "\uD83D\uDCDD 로깅 — logback.xml",               "3000"},
            {"logback-spring.xml",     "\uD83D\uDCDD 로깅 — logback-spring.xml",        "3000"},
            {"globals.properties",     "\uD83D\uDD0C 외부 연동 — globals.properties",    "3000"},
            {"egov-com-servlet.xml",   "\uD83D\uDEA8 예외 처리 — egov-com-servlet.xml",  "3000"},
        };

        boolean hasAny = false;
        for (int t = 0; t < infraTargets.length; t++) {
            String pattern = infraTargets[t][0];
            String title = infraTargets[t][1];
            int maxLen = Integer.parseInt(infraTargets[t][2]);

            for (int d = 0; d < scanDirs.size(); d++) {
                File dir = (File) scanDirs.get(d);
                File found = findFileDeep(dir, pattern, 8);
                if (found != null) {
                    String content = readFileContent(found, maxLen);
                    if (content.length() > 0) {
                        String ext = pattern.endsWith(".java") ? "java" :
                                     pattern.endsWith(".xml") ? "xml" :
                                     pattern.endsWith(".properties") ? "properties" : "";
                        sb.append("### ").append(title).append("\n");
                        sb.append("\uD83D\uDCC1 ").append(found.getAbsolutePath()).append("\n");
                        sb.append("```").append(ext).append("\n").append(content).append("\n```\n\n");
                        hasAny = true;
                        break; // 같은 패턴의 파일은 첫 번째 발견된 것만
                    }
                }
            }
        }

        // @ControllerAdvice 클래스 탐색
        for (int d = 0; d < scanDirs.size(); d++) {
            File dir = (File) scanDirs.get(d);
            java.util.List javaFiles = new ArrayList();
            findAllFiles(dir, ".java", javaFiles, 0, 300);
            for (int i = 0; i < javaFiles.size(); i++) {
                File jf = (File) javaFiles.get(i);
                String content = readFileContent(jf, 80000);
                if (content.contains("@ControllerAdvice") || content.contains("@RestControllerAdvice")) {
                    sb.append("### \uD83D\uDEA8 예외 처리 — ").append(jf.getName()).append("\n");
                    sb.append("\uD83D\uDCC1 ").append(relativePath(jf, dir)).append("\n");
                    sb.append("```java\n").append(content.length() > 5000 ? content.substring(0, 5000) : content)
                      .append("\n```\n\n");
                    hasAny = true;
                }
            }
        }

        if (!hasAny) {
            sb.append("_\uc778\ud504\ub77c \uc124\uc815 \ud30c\uc77c\uc744 \ucc3e\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4._\n\n");
        }
    }

    /**
     * AI 응답에서 파일 경로를 클릭했을 때 Eclipse 에디터에서 해당 파일을 여는 메서드.
     * 프로젝트 상대 경로를 받아 workspace 내에서 찾아서 연다.
     */
    /* ═══════════════════════════════════════════════════════
     *  PL 워크플로우 — nori:// URL 핸들러
     * ═══════════════════════════════════════════════════════ */

    /**
     * nori:// 스킴 URL 처리.
     * 형식: nori://action?param1=val1&param2=val2
     */
    private void handleNoriUrl(final String url) {
        try {
            // nori:// 뒤의 action 부분 추출
            String afterScheme = url.substring("nori://".length());
            int qIdx = afterScheme.indexOf('?');
            String action = qIdx >= 0 ? afterScheme.substring(0, qIdx) : afterScheme;
            String queryStr = qIdx >= 0 ? afterScheme.substring(qIdx + 1) : "";

            // 파라미터 파싱
            final java.util.Map params = new java.util.HashMap();
            if (queryStr.length() > 0) {
                String[] pairs = queryStr.split("&");
                for (int i = 0; i < pairs.length; i++) {
                    int eqIdx = pairs[i].indexOf('=');
                    if (eqIdx >= 0) {
                        String key = pairs[i].substring(0, eqIdx);
                        String val = java.net.URLDecoder.decode(pairs[i].substring(eqIdx + 1), "UTF-8");
                        params.put(key, val);
                    }
                }
            }

            if ("retry".equals(action)) {
                handlePlRetry(params);
            } else if ("feedback".equals(action)) {
                handlePlFeedback(params);
            } else if ("open".equals(action)) {
                String file = (String) params.get("file");
                if (file != null) openProjectFile(file);
            } else if ("test-with-deps".equals(action)) {
                handlePlTestWithDeps(params);
            } else if ("test-skip".equals(action)) {
                handlePlTestSkip(params);
            } else if ("pl-confirm".equals(action)) {
                handlePlConfirm(params);
            } else if ("pl-cancel".equals(action)) {
                handlePlCancel();
            }
        } catch (Exception e) {
            System.out.println("[NORI-PL] URL 처리 오류: " + e.getMessage());
        }
    }

    /** PL 소스 다시 생성 */
    private void handlePlRetry(final java.util.Map params) {
        final String todoId = (String) params.get("todoId");
        final String orderStr = (String) params.get("order");
        if (todoId == null || orderStr == null) return;

        addStepOnUI("pl-retry", "소스 다시 생성 중...");

        Job job = new Job("Nori PL - 소스 재생성") {
            protected IStatus run(IProgressMonitor monitor) {
                try {
                    int order = Integer.parseInt(orderStr);
                    String result = NoriApiClient.getInstance()
                            .plRetrySource(todoId, order, null);
                    completeStepOnUI("pl-retry", "소스 재생성 완료");

                    // 결과 파싱하여 소스 카드로 표시
                    renderPlSuggestResult(result, todoId, orderStr);
                } catch (Exception e) {
                    final String err = e.getMessage();
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            failStepDirect("pl-retry", "재생성 실패: " + err);
                        }
                    });
                }
                return Status.OK_STATUS;
            }
        };
        job.setUser(false);
        job.schedule();
    }

    /** PL 피드백 저장 (좋아요/안좋아요) — todoId 또는 filePath 기반 */
    private void handlePlFeedback(final java.util.Map params) {
        final String todoId = (String) params.get("todoId");
        final String orderStr = (String) params.get("order");
        final String type = (String) params.get("type");
        final String fileName = (String) params.get("file");
        final String reason = (String) params.get("reason");
        if (type == null) return;

        if (todoId != null && todoId.length() > 0) {
            Job job = new Job("Nori PL - \uD53C\uB4DC\uBC31 \uC800\uC7A5") {
                protected IStatus run(IProgressMonitor monitor) {
                    try {
                        int order = orderStr != null ? Integer.parseInt(orderStr) : 0;
                        NoriApiClient.getInstance()
                                .plSaveFeedback(todoId, order, fileName != null ? fileName : "",
                                        type, reason);
                    } catch (Exception e) {
                        System.out.println("[NORI-PL] \uD53C\uB4DC\uBC31 \uC800\uC7A5 \uC2E4\uD328: " + e.getMessage());
                    }
                    return Status.OK_STATUS;
                }
            };
            job.setUser(false);
            job.schedule();
        } else if (fileName != null && fileName.length() > 0) {
            final String feedbackMsg = "like".equals(type) ? "\uD83D\uDC4D" : "\uD83D\uDC4E";
            addSystemMessageOnUI(feedbackMsg + " \uD53C\uB4DC\uBC31 \uAE30\uB85D: " + fileName
                    + (reason != null && reason.length() > 0 ? " \u2014 " + reason : ""));
        }
    }

    /** 의존성 제공 후 테스트 재실행 */
    private void handlePlTestWithDeps(final java.util.Map params) {
        final String todoId = (String) params.get("todoId");
        final String orderStr = (String) params.get("order");
        if (todoId == null || orderStr == null) return;

        addStepOnUI("pl-test-deps", "의존성 확인 후 테스트 실행 중...");

        Job job = new Job("Nori PL - 의존성 테스트") {
            protected IStatus run(IProgressMonitor monitor) {
                try {
                    int order = Integer.parseInt(orderStr);
                    NoriApiClient.getInstance()
                            .plUpdateTodoItem(todoId, order, "testing");
                    completeStepOnUI("pl-test-deps", "테스트 완료");
                } catch (Exception e) {
                    final String err = e.getMessage();
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            failStepDirect("pl-test-deps", "테스트 실패: " + err);
                        }
                    });
                }
                return Status.OK_STATUS;
            }
        };
        job.setUser(false);
        job.schedule();
    }

    /** 의존성 테스트 건너뛰기 */
    private void handlePlTestSkip(final java.util.Map params) {
        final String todoId = (String) params.get("todoId");
        final String orderStr = (String) params.get("order");
        if (todoId == null || orderStr == null) return;

        Job job = new Job("Nori PL - 테스트 건너뛰기") {
            protected IStatus run(IProgressMonitor monitor) {
                try {
                    int order = Integer.parseInt(orderStr);
                    NoriApiClient.getInstance()
                            .plUpdateTodoItem(todoId, order, "skipped");
                } catch (Exception e) {
                    System.out.println("[NORI-PL] 테스트 스킵 실패: " + e.getMessage());
                }
                return Status.OK_STATUS;
            }
        };
        job.setUser(false);
        job.schedule();
    }

    /** PL 소스 제안 결과를 파싱하여 소스 카드 HTML로 표시 */
    private void renderPlSuggestResult(String json, String todoId, String order) {
        String fileName = NoriApiClient.extractJsonField(json, "file_name");
        String filePath = NoriApiClient.extractJsonField(json, "file_path");
        String source = NoriApiClient.extractJsonField(json, "source");
        String startLine = NoriApiClient.extractJsonField(json, "start_line");
        String description = NoriApiClient.extractJsonField(json, "description");

        final StringBuilder cardHtml = new StringBuilder();
        cardHtml.append("<div class='nori-source-card' data-todo-id='")
                .append(escapeHtml(todoId != null ? todoId : "")).append("' data-order='")
                .append(escapeHtml(order != null ? order : "")).append("'>")
                .append("<div class='nori-file-info'>")
                .append("  <div class='file-name'>\uD83D\uDCC4 ").append(escapeHtml(fileName != null ? fileName : "")).append("</div>")
                .append("  <a class='file-path' href=\"javascript:openFileInProject('")
                .append(NoriApiClient.escapeJson(filePath != null ? filePath : "")).append("')\">")
                .append("    \uD83D\uDCC2 ").append(escapeHtml(filePath != null ? filePath : "")).append("</a>")
                .append("  <div class='file-line'>\uD83D\uDCCD 시작 라인: ").append(startLine != null ? startLine : "?").append("</div>")
                .append("</div>");
        if (description != null && description.length() > 0) {
            cardHtml.append("<div class='nori-reason-box'>").append(escapeHtml(description)).append("</div>");
        }
        cardHtml.append("<div class='nori-source-box'><pre><code>")
                .append(escapeHtml(source != null ? source : "")).append("</code></pre></div>")
                .append("<div class='nori-source-actions'>")
                .append("  <div class='action-left'>")
                .append("    <button class='action-btn' onclick='onRetry(this)' title='다시 생성'>\uD83D\uDD04</button>")
                .append("    <button class='action-btn' onclick='onLike(this)' title='좋아요'>\uD83D\uDC4D</button>")
                .append("    <button class='action-btn' onclick='onDislike(this)' title='안좋아요'>\uD83D\uDC4E</button>")
                .append("  </div>")
                .append("  <div class='action-right'>")
                .append("    <button class='copy-btn' onclick='copySource(this)' title='소스 복사'><span class='copy-icon'>\uD83D\uDCCB</span></button>")
                .append("  </div>")
                .append("</div></div>");

        Display.getDefault().asyncExec(new Runnable() {
            public void run() {
                messages.add(new String[]{"pl-card", cardHtml.toString(), null, nowTimestamp()});
                refreshDisplay();
            }
        });
    }

    /** PL 워크플로우 시작: 사용자 요청 분석 → TODO 생성 → 순차 소스 제안 */
    public void startPlWorkflow(final String request) {
        messages.add(new String[]{"user", "\uD83D\uDCCB PL 작업 요청: " + request, null, nowTimestamp()});
        refreshDisplay();

        Job job = new Job("Nori PL - 워크플로우") {
            protected IStatus run(IProgressMonitor monitor) {
                try {
                    final NoriApiClient api = NoriApiClient.getInstance();
                    File projectDir = getActiveProjectDir();

                    // ── Step 0: 프로젝트 컨텍스트 + 의존관계 맵 구축 ──
                    addStepOnUI("pl-dep-map", "프로젝트 의존관계 맵 구축 중...");

                    String projectProfile = projectDir != null
                            ? collectProjectContextFromDir(projectDir) : "";
                    String fileTree = projectDir != null
                            ? buildProfileTree(projectDir, "", 0) : "";
                    if (fileTree.length() > 6000) fileTree = fileTree.substring(0, 6000);
                    String dependencyMap = projectDir != null
                            ? buildDependencyMap(projectDir) : "{}";

                    completeStepOnUI("pl-dep-map", "의존관계 맵 구축 완료");

                    // ── Step 1: 분석 + TODO 생성 ──
                    addStepOnUI("pl-analyze", "AI 분석 중... (파일 탐지 + 순서 결정)");

                    String result = api.plAnalyzeAndCreate(
                            request, projectProfile, fileTree, dependencyMap);

                    if (result == null || result.startsWith("에러") || result.startsWith("서버")) {
                        completeStepOnUI("pl-analyze", "분석 실패");
                        addAssistantOnUI("분석에 실패했어. 서버 상태를 확인해줘!\n" + (result != null ? result : ""));
                        return Status.OK_STATUS;
                    }

                    // ── AI thinking 표시 (분석 과정을 사용자에게 보여줌) ──
                    List thinkingSteps = extractJsonArray(result, "thinking");
                    if (!thinkingSteps.isEmpty()) {
                        for (int t = 0; t < thinkingSteps.size(); t++) {
                            final String thought = (String) thinkingSteps.get(t);
                            addThinkingOnUI(thought);
                            try { Thread.sleep(300); } catch (InterruptedException ie) { break; }
                        }
                    }

                    String todoId = NoriApiClient.extractJsonField(result, "todo_id");
                    completeStepOnUI("pl-analyze", "TODO 생성 완료 (ID: " + todoId + ")");

                    // 자동 추가된 파일이 있으면 알려주기
                    List autoAdded = extractJsonArray(result, "auto_added");
                    if (!autoAdded.isEmpty()) {
                        StringBuilder autoMsg = new StringBuilder();
                        autoMsg.append("\uD83D\uDD0D 패턴 매칭으로 자동 추가된 파일:\n");
                        for (int a = 0; a < autoAdded.size(); a++) {
                            autoMsg.append("  + ").append((String) autoAdded.get(a)).append("\n");
                        }
                        addThinkingOnUI(autoMsg.toString().trim());
                    }

                    // items 배열에서 파일 목록 추출
                    String itemsJson = extractRawJsonArray(result, "items");
                    List fileNames = extractJsonArray(itemsJson, "file_name");

                    if (fileNames.isEmpty()) {
                        addAssistantOnUI("분석 결과 수정할 파일이 없어!");
                        return Status.OK_STATUS;
                    }

                    // ── Step 2: 순차적 소스 제안 ──
                    for (int i = 0; i < fileNames.size(); i++) {
                        String fname = (String) fileNames.get(i);
                        int order = i + 1;
                        addStepOnUI("pl-suggest-" + order, "소스 생성 중: " + fname);

                        // 파일 내용 읽기 (있으면)
                        String fileContent = "";
                        if (projectDir != null) {
                            fileContent = readFileContent(projectDir, fname);
                        }

                        String suggestResult = api.plSuggestSource(todoId, order, fileContent);
                        completeStepOnUI("pl-suggest-" + order, "소스 생성 완료: " + fname);

                        renderPlSuggestResult(suggestResult, todoId, String.valueOf(order));
                    }

                    // ── Step 3: 보고서 생성 ──
                    addStepOnUI("pl-report", "보고서 생성 중...");
                    api.plGenerateReport(todoId);
                    completeStepOnUI("pl-report", "PL 워크플로우 완료");

                } catch (Exception e) {
                    final String err = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            messages.add(new String[]{"assistant",
                                    "\u274C PL 워크플로우 오류: " + err, null, nowTimestamp()});
                            refreshDisplay();
                        }
                    });
                }
                return Status.OK_STATUS;
            }
        };
        job.setUser(false);
        job.schedule();
    }

    /** AI의 사고 과정을 UI에 표시 (thinking 스타일) */
    private void addThinkingOnUI(final String thought) {
        Display.getDefault().syncExec(new Runnable() {
            public void run() {
                String prefix = "";
                if (thought.startsWith("생각:")) prefix = "🧠 ";
                else if (thought.startsWith("행동:")) prefix = "⚡ ";
                else if (thought.startsWith("관찰:")) prefix = "👀 ";
                else if (thought.startsWith("수정:")) prefix = "🔄 ";
                messages.add(new String[]{"thinking", prefix + thought});
                refreshDisplay();
            }
        });
    }

    /** 프로젝트 파일 내용 읽기 (상대 경로 기반) */
    private String readFileContent(File projectDir, String fileName) {
        // 프로젝트 내에서 파일 검색
        File found = findFileRecursive(projectDir, fileName, 5);
        if (found == null || !found.exists()) return "";
        try {
            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(new FileInputStream(found), FILE_UTF8));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                sb.append(line).append("\n");
            }
            reader.close();
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /** 디렉토리에서 파일명으로 재귀 검색 (최대 depth 제한) */
    private File findFileRecursive(File dir, String fileName, int maxDepth) {
        if (maxDepth <= 0 || dir == null || !dir.isDirectory()) return null;
        File[] children = dir.listFiles();
        if (children == null) return null;
        for (int i = 0; i < children.length; i++) {
            if (children[i].isFile() && children[i].getName().equals(fileName)) {
                return children[i];
            }
        }
        for (int i = 0; i < children.length; i++) {
            if (children[i].isDirectory()
                    && !children[i].getName().startsWith(".")
                    && !"target".equals(children[i].getName())
                    && !"bin".equals(children[i].getName())
                    && !"build".equals(children[i].getName())
                    && !"node_modules".equals(children[i].getName())) {
                File result = findFileRecursive(children[i], fileName, maxDepth - 1);
                if (result != null) return result;
            }
        }
        return null;
    }

    private void openProjectFile(String relativePath) {
        openProjectFile(relativePath, -1);
    }

    private void openProjectFile(String relativePath, int gotoLine) {
        try {
            // 경로 정리
            String path = relativePath.trim();
            if (path.startsWith("/") || path.startsWith("\\")) {
                path = path.substring(1);
            }
            System.out.println("[NORI-OPEN] 파일 열기 시도: " + path);

            // 1차: Eclipse IProject 기준으로 찾기
            IProject[] projects = ResourcesPlugin.getWorkspace().getRoot().getProjects();
            for (int pi = 0; pi < projects.length; pi++) {
                if (!projects[pi].isOpen()) continue;
                org.eclipse.core.resources.IFile file = projects[pi].getFile(new org.eclipse.core.runtime.Path(path));
                if (file != null && file.exists()) {
                    IWorkbenchPage page = PlatformUI.getWorkbench()
                            .getActiveWorkbenchWindow().getActivePage();
                    if (page != null) {
                        IEditorPart part = org.eclipse.ui.ide.IDE.openEditor(page, file);
                        gotoLineInEditor(part, gotoLine);
                        System.out.println("[NORI-OPEN] IProject 매칭 성공: " + file.getFullPath());
                    }
                    return;
                }
            }

            // 2차: 프로젝트 루트 기준 절대경로로 재시도
            File projectDir = getActiveProjectDir();
            if (projectDir != null) {
                File target = new File(projectDir, path);
                if (target.exists()) {
                    if (openFileByAbsolutePath(target, gotoLine)) return;
                }
            }

            // 3차: 경로 끝부분 매칭 (빠른 fallback — 재귀 검색 대신)
            System.out.println("[NORI-OPEN] 직접 매칭 실패 → 부분경로 검색: " + path);

            List searchDirs = new ArrayList();
            if (projectDir != null) searchDirs.add(projectDir);
            for (int pi = 0; pi < projects.length; pi++) {
                if (projects[pi].isOpen() && projects[pi].getLocation() != null) {
                    File d = projects[pi].getLocation().toFile();
                    if (!searchDirs.contains(d)) searchDirs.add(d);
                }
            }
            String tryPath = path;
            boolean found = false;
            while (!found && tryPath.contains("/")) {
                tryPath = tryPath.substring(tryPath.indexOf('/') + 1);
                String[] prefixes = {"src/main/java/", "src/main/resources/", "src/main/webapp/"};
                for (int di = 0; di < searchDirs.size() && !found; di++) {
                    for (int pi2 = 0; pi2 < prefixes.length && !found; pi2++) {
                        File candidate = new File((File) searchDirs.get(di),
                                (prefixes[pi2] + tryPath).replace('/', File.separatorChar));
                        if (candidate.exists() && candidate.isFile()) {
                            System.out.println("[NORI-OPEN] 부분경로 매칭 성공: " + candidate.getAbsolutePath());
                            if (openFileByAbsolutePath(candidate, gotoLine)) { found = true; }
                        }
                    }
                }
            }
            if (!found) {
                System.err.println("[NORI-OPEN] 최종 실패 — 파일 못 찾음: " + relativePath);
            }
        } catch (Exception e) {
            System.err.println("[NORI-OPEN] 파일 열기 실패: " + relativePath + " - " + e.getMessage());
        }
    }

    /** 절대경로 File 객체를 Eclipse 에디터에서 열기 (gotoLine > 0이면 해당 라인으로 이동) */
    private boolean openFileByAbsolutePath(File target) {
        return openFileByAbsolutePath(target, -1);
    }

    private boolean openFileByAbsolutePath(File target, int gotoLine) {
        try {
            org.eclipse.core.runtime.IPath ipath = new org.eclipse.core.runtime.Path(target.getAbsolutePath());
            org.eclipse.core.resources.IFile wsFile = ResourcesPlugin.getWorkspace().getRoot().getFileForLocation(ipath);
            if (wsFile != null && wsFile.exists()) {
                IWorkbenchPage page = PlatformUI.getWorkbench()
                        .getActiveWorkbenchWindow().getActivePage();
                if (page != null) {
                    IEditorPart part = org.eclipse.ui.ide.IDE.openEditor(page, wsFile);
                    gotoLineInEditor(part, gotoLine);
                    System.out.println("[NORI-OPEN] 절대경로 매칭 성공: " + target.getAbsolutePath());
                    return true;
                }
            }
        } catch (Exception e) {
            System.err.println("[NORI-OPEN] 절대경로 열기 실패: " + e.getMessage());
        }
        return false;
    }

    /** 에디터에서 지정한 라인으로 커서 이동 */
    private void gotoLineInEditor(IEditorPart part, int line) {
        if (part == null || line <= 0) return;
        try {
            ITextEditor te = (ITextEditor) part.getAdapter(ITextEditor.class);
            if (te == null) return;
            IDocument doc = te.getDocumentProvider().getDocument(te.getEditorInput());
            if (doc == null) return;
            int offset = doc.getLineOffset(Math.min(line - 1, doc.getNumberOfLines() - 1));
            te.selectAndReveal(offset, 0);
        } catch (Exception e) { /* ignore */ }
    }

    // ── 프로파일 생성 ──

    private String generateProjectProfile(File projectDir) {
        StringBuilder sb = new StringBuilder();
        sb.append("# \ud504\ub85c\uc81d\ud2b8 \ud504\ub85c\ud30c\uc77c: ").append(projectDir.getName()).append("\n\n");

        // 1) 파일 구조
        sb.append("## \ud30c\uc77c \uad6c\uc870\n```\n");
        String tree = buildProfileTree(projectDir, "", 0);
        if (tree.length() > 6000) tree = tree.substring(0, 6000) + "\n...(\uc774\ud558 \uc0dd\ub7b5)\n";
        sb.append(tree).append("```\n\n");

        // 2) 설정 파일
        sb.append("## \uc124\uc815 \ud30c\uc77c\n\n");
        appendConfigSection(sb, projectDir, "pom.xml", 5000);
        appendConfigSection(sb, projectDir, "build.gradle", 3000);
        File webXml = findFileDeep(projectDir, "web.xml", 5);
        if (webXml != null) {
            sb.append("### web.xml\n```xml\n").append(readFileContent(webXml, 3000)).append("\n```\n\n");
        }
        File appProps = findFileDeep(projectDir, "application.properties", 5);
        if (appProps != null) {
            sb.append("### application.properties\n```\n").append(readFileContent(appProps, 2000)).append("\n```\n\n");
        } else {
            File appYml = findFileDeep(projectDir, "application.yml", 5);
            if (appYml != null) {
                sb.append("### application.yml\n```yaml\n").append(readFileContent(appYml, 2000)).append("\n```\n\n");
            }
        }

        // 3) Java 클래스 분석
        List javaFiles = new ArrayList();
        findAllFiles(projectDir, ".java", javaFiles, 0, 200);

        List controllers = new ArrayList();
        List services = new ArrayList();
        List daos = new ArrayList();
        List vos = new ArrayList();
        List others = new ArrayList();

        for (int i = 0; i < javaFiles.size(); i++) {
            File f = (File) javaFiles.get(i);
            String name = f.getName();
            if (name.endsWith("Controller.java") || name.endsWith("Action.java")) {
                controllers.add(f);
            } else if (name.endsWith("Service.java") || name.endsWith("ServiceImpl.java")) {
                services.add(f);
            } else if (name.endsWith("Dao.java") || name.endsWith("DAO.java")
                    || name.endsWith("Mapper.java") || name.endsWith("Repository.java")) {
                daos.add(f);
            } else if (name.endsWith("Vo.java") || name.endsWith("VO.java")
                    || name.endsWith("Dto.java") || name.endsWith("DTO.java")
                    || name.endsWith("Entity.java")) {
                vos.add(f);
            } else {
                others.add(f);
            }
        }

        appendJavaCategory(sb, "Controller", controllers, projectDir);
        appendJavaCategory(sb, "Service", services, projectDir);
        appendJavaCategory(sb, "DAO", daos, projectDir);
        appendJavaCategory(sb, "VO/DTO", vos, projectDir);
        if (others.size() > 0 && others.size() <= 30) {
            appendJavaCategory(sb, "\uae30\ud0c0 Java", others, projectDir);
        }

        // 4) MyBatis Mapper XML
        List mapperXmls = new ArrayList();
        findAllFiles(projectDir, "Mapper.xml", mapperXmls, 0, 50);
        findAllFiles(projectDir, "_SQL.xml", mapperXmls, 0, 50);
        findAllFiles(projectDir, "_sql.xml", mapperXmls, 0, 50);
        // mybatis_sql_*.xml 패턴 (eGovFrame 관례) — 중복 제거
        List allXmls = new ArrayList();
        findAllFiles(projectDir, ".xml", allXmls, 0, 200);
        for (int i = 0; i < allXmls.size(); i++) {
            File xf = (File) allXmls.get(i);
            String xfName = xf.getName().toLowerCase();
            if ((xfName.startsWith("mybatis") || xfName.contains("_sql_"))
                    && !mapperXmls.contains(xf)) {
                mapperXmls.add(xf);
            }
        }
        if (mapperXmls.size() > 0) {
            sb.append("## MyBatis Mapper (").append(mapperXmls.size()).append("\uac1c)\n\n");
            for (int i = 0; i < mapperXmls.size(); i++) {
                appendMapperInfo(sb, (File) mapperXmls.get(i), projectDir);
            }
            sb.append("\n");
        }

        // 5) JSP 페이지
        List jspFiles = new ArrayList();
        findAllFiles(projectDir, ".jsp", jspFiles, 0, 100);
        if (jspFiles.size() > 0) {
            sb.append("## JSP \ud398\uc774\uc9c0 (").append(jspFiles.size()).append("\uac1c)\n\n");
            for (int i = 0; i < jspFiles.size(); i++) {
                sb.append("- ").append(relativePath((File) jspFiles.get(i), projectDir)).append("\n");
            }
            sb.append("\n");
        }

        // 6) SQL 파일
        List sqlFiles = new ArrayList();
        findAllFiles(projectDir, ".sql", sqlFiles, 0, 20);
        if (sqlFiles.size() > 0) {
            sb.append("## SQL \ud30c\uc77c\n\n");
            for (int i = 0; i < sqlFiles.size(); i++) {
                sb.append("- ").append(relativePath((File) sqlFiles.get(i), projectDir)).append("\n");
            }
            sb.append("\n");
        }

        return sb.toString();
    }

    /**
     * 의존관계 맵 구축 — 도메인별 관련 파일을 그룹화하여 JSON 문자열로 반환.
     * 패턴 기반: Controller/Service/DAO/VO/Mapper/JSP를 도메인 접두사로 묶음.
     * 예: "BoardMail" → { vo: [...], dao: [...], service: [...], controller: [...] }
     */
    private String buildDependencyMap(File projectDir) {
        if (projectDir == null || !projectDir.isDirectory()) return "{}";

        // 1) 모든 Java/XML/JSP 파일 수집
        List javaFiles = new ArrayList();
        findAllFiles(projectDir, ".java", javaFiles, 0, 300);
        List xmlFiles = new ArrayList();
        findAllFiles(projectDir, ".xml", xmlFiles, 0, 200);
        List jspFiles = new ArrayList();
        findAllFiles(projectDir, ".jsp", jspFiles, 0, 100);

        // 2) Java 파일에서 도메인 접두사 추출 (레이어 접미사 제거)
        // 예: BoardMailController.java → "BoardMail", BoardMailServiceImpl.java → "BoardMail"
        String[] suffixes = {
            "Controller", "Action",
            "ServiceImpl", "Service",
            "DAO", "Dao", "Mapper", "Repository",
            "VO", "Vo", "DTO", "Dto", "Entity"
        };
        String[] layerKeys = {
            "controller", "controller",
            "service", "service",
            "dao", "dao", "dao", "dao",
            "vo", "vo", "vo", "vo", "vo"
        };

        // domain → { layer → [{name, path}] }
        java.util.Map domainMap = new java.util.LinkedHashMap();

        for (int i = 0; i < javaFiles.size(); i++) {
            File f = (File) javaFiles.get(i);
            String name = f.getName();
            if (!name.endsWith(".java")) continue;
            String baseName = name.substring(0, name.length() - 5); // remove .java

            String domainKey = null;
            String layerKey = null;

            for (int s = 0; s < suffixes.length; s++) {
                if (baseName.endsWith(suffixes[s]) && baseName.length() > suffixes[s].length()) {
                    domainKey = baseName.substring(0, baseName.length() - suffixes[s].length());
                    layerKey = layerKeys[s];
                    break;
                }
            }

            if (domainKey == null || domainKey.length() < 2) continue;

            java.util.Map group = (java.util.Map) domainMap.get(domainKey);
            if (group == null) {
                group = new java.util.LinkedHashMap();
                domainMap.put(domainKey, group);
            }

            List entries = (List) group.get(layerKey);
            if (entries == null) {
                entries = new ArrayList();
                group.put(layerKey, entries);
            }

            // 중복 방지
            boolean exists = false;
            for (int e = 0; e < entries.size(); e++) {
                String[] entry = (String[]) entries.get(e);
                if (entry[0].equals(name)) { exists = true; break; }
            }
            if (!exists) {
                entries.add(new String[]{ name, relativePath(f, projectDir) });
            }
        }

        // 3) 매퍼 XML 매칭 — 도메인 이름이 포함된 XML을 해당 그룹에 추가
        java.util.Iterator domainIt = domainMap.entrySet().iterator();
        List domainKeys = new ArrayList();
        while (domainIt.hasNext()) {
            java.util.Map.Entry e = (java.util.Map.Entry) domainIt.next();
            domainKeys.add((String) e.getKey());
        }

        for (int x = 0; x < xmlFiles.size(); x++) {
            File xf = (File) xmlFiles.get(x);
            String xfNameLower = xf.getName().toLowerCase();
            if (!xfNameLower.contains("mapper") && !xfNameLower.contains("_sql")
                    && !xfNameLower.contains("mybatis")) continue;

            for (int d = 0; d < domainKeys.size(); d++) {
                String dk = (String) domainKeys.get(d);
                if (xfNameLower.contains(dk.toLowerCase())) {
                    java.util.Map group = (java.util.Map) domainMap.get(dk);
                    List entries = (List) group.get("mapper_xml");
                    if (entries == null) {
                        entries = new ArrayList();
                        group.put("mapper_xml", entries);
                    }
                    entries.add(new String[]{ xf.getName(), relativePath(xf, projectDir) });
                    break;
                }
            }
        }

        // 4) JSP 매칭 — 도메인 이름 소문자가 포함된 JSP를 해당 그룹에 추가
        for (int j = 0; j < jspFiles.size(); j++) {
            File jf = (File) jspFiles.get(j);
            String jfNameLower = jf.getName().toLowerCase();

            for (int d = 0; d < domainKeys.size(); d++) {
                String dk = (String) domainKeys.get(d);
                if (jfNameLower.contains(dk.toLowerCase())) {
                    java.util.Map group = (java.util.Map) domainMap.get(dk);
                    List entries = (List) group.get("jsp");
                    if (entries == null) {
                        entries = new ArrayList();
                        group.put("jsp", entries);
                    }
                    entries.add(new String[]{ jf.getName(), relativePath(jf, projectDir) });
                    break;
                }
            }
        }

        // 5) 단일 파일만 있는 도메인은 제거 (의미있는 그룹만 유지)
        List removeKeys = new ArrayList();
        domainIt = domainMap.entrySet().iterator();
        while (domainIt.hasNext()) {
            java.util.Map.Entry e = (java.util.Map.Entry) domainIt.next();
            java.util.Map group = (java.util.Map) e.getValue();
            int totalFiles = 0;
            java.util.Iterator gi = group.values().iterator();
            while (gi.hasNext()) {
                totalFiles += ((List) gi.next()).size();
            }
            if (totalFiles <= 1) {
                removeKeys.add(e.getKey());
            }
        }
        for (int r = 0; r < removeKeys.size(); r++) {
            domainMap.remove(removeKeys.get(r));
        }

        // 6) JSON 직렬화 (수동 — Java 8 호환)
        return buildDependencyMapJson(domainMap);
    }

    /** 의존관계 맵을 JSON 문자열로 변환 (Java 8 수동 직렬화) */
    private String buildDependencyMapJson(java.util.Map domainMap) {
        StringBuilder json = new StringBuilder();
        json.append("{");

        boolean firstDomain = true;
        java.util.Iterator it = domainMap.entrySet().iterator();
        while (it.hasNext()) {
            java.util.Map.Entry domainEntry = (java.util.Map.Entry) it.next();
            if (!firstDomain) json.append(",");
            firstDomain = false;

            json.append("\"").append(escapeJsonStr((String) domainEntry.getKey())).append("\":{");

            java.util.Map group = (java.util.Map) domainEntry.getValue();
            boolean firstLayer = true;
            java.util.Iterator gi = group.entrySet().iterator();
            while (gi.hasNext()) {
                java.util.Map.Entry layerEntry = (java.util.Map.Entry) gi.next();
                if (!firstLayer) json.append(",");
                firstLayer = false;

                json.append("\"").append(escapeJsonStr((String) layerEntry.getKey())).append("\":[");

                List entries = (List) layerEntry.getValue();
                for (int e = 0; e < entries.size(); e++) {
                    if (e > 0) json.append(",");
                    String[] entry = (String[]) entries.get(e);
                    json.append("{\"name\":\"").append(escapeJsonStr(entry[0]))
                        .append("\",\"path\":\"").append(escapeJsonStr(entry[1]))
                        .append("\"}");
                }
                json.append("]");
            }
            json.append("}");
        }

        json.append("}");
        return json.toString();
    }

    /** JSON 문자열 이스케이프 */
    private String escapeJsonStr(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    private void appendJavaCategory(StringBuilder sb, String category, List files, File projectDir) {
        if (files.isEmpty()) return;
        sb.append("## ").append(category).append(" (").append(files.size()).append("\uac1c)\n\n");
        for (int i = 0; i < files.size(); i++) {
            sb.append(extractJavaInfo((File) files.get(i), projectDir));
        }
    }

    private String extractJavaInfo(File f, File projectDir) {
        String content = readFileContent(f, 200000);
        if (content.length() == 0) return "";
        String[] lines = content.split("\n");
        StringBuilder info = new StringBuilder();
        info.append("### ").append(f.getName()).append("\n");
        info.append("\uacbd\ub85c: ").append(relativePath(f, projectDir)).append("\n");

        // 파일명에서 비즈니스 기능 추론 (규칙 기반 설명)
        String desc = guessClassDescription(f.getName(), content);
        if (desc.length() > 0) {
            info.append("\uc124\uba85: ").append(desc).append("\n");
        }

        String pendingAnnotation = "";
        boolean inBlockComment = false;

        for (int i = 0; i < lines.length; i++) {
            String line = lines[i].trim();

            if (line.startsWith("/*")) inBlockComment = true;
            if (line.contains("*/")) { inBlockComment = false; continue; }
            if (inBlockComment || line.startsWith("//") || line.startsWith("*")) continue;

            if (line.startsWith("package ")) {
                info.append(line).append("\n");
                continue;
            }

            if (line.startsWith("@")) {
                if (isImportantAnnotation(line)) {
                    pendingAnnotation = line;
                }
                continue;
            }

            if (isClassDeclaration(line)) {
                if (pendingAnnotation.length() > 0) {
                    info.append(pendingAnnotation).append("\n");
                    pendingAnnotation = "";
                }
                int braceIdx = line.indexOf('{');
                String decl = braceIdx >= 0 ? line.substring(0, braceIdx).trim() : line;
                info.append(decl).append("\n");
                continue;
            }

            if (isMethodSignature(line)) {
                if (pendingAnnotation.length() > 0) {
                    info.append("  ").append(pendingAnnotation).append("\n");
                    pendingAnnotation = "";
                }
                String methodLine = line;
                if (methodLine.endsWith("{")) {
                    methodLine = methodLine.substring(0, methodLine.length() - 1).trim();
                }
                info.append("  ").append(methodLine).append("\n");
                continue;
            }

            if (isFieldDeclaration(line)) {
                info.append("  ").append(line).append("\n");
                continue;
            }

            if (line.length() > 0) pendingAnnotation = "";
        }

        info.append("\n");
        return info.toString();
    }

    private boolean isImportantAnnotation(String line) {
        return line.startsWith("@Controller") || line.startsWith("@RestController")
                || line.startsWith("@Service") || line.startsWith("@Repository")
                || line.startsWith("@Component") || line.startsWith("@RequestMapping")
                || line.startsWith("@GetMapping") || line.startsWith("@PostMapping")
                || line.startsWith("@PutMapping") || line.startsWith("@DeleteMapping")
                || line.startsWith("@Transactional") || line.startsWith("@Table")
                || line.startsWith("@Entity") || line.startsWith("@Override");
    }

    /** API 에러 응답 문자열인지 확인 */
    private boolean isApiErrorResponse(String resp) {
        if (resp == null || resp.length() == 0) return true;
        return resp.startsWith("\uc5d0\ub7ec") || resp.startsWith("\uc11c\ubc84")
                || resp.startsWith("\uc624\ub958") || resp.startsWith("\uc694\uccad")
                || resp.startsWith("\uc751\ub2f5\uc744 \ud30c\uc2f1");
    }

    /** 파일명과 내용에서 비즈니스 기능을 추론 (규칙 기반) */
    private String guessClassDescription(String fileName, String content) {
        // 1) JavaDoc 클래스 설명 추출 시도
        int classDocIdx = content.indexOf("/**");
        if (classDocIdx >= 0) {
            int docEnd = content.indexOf("*/", classDocIdx);
            if (docEnd > classDocIdx) {
                String doc = content.substring(classDocIdx + 3, docEnd).trim();
                // 첫 줄만 추출 (@ 태그 전까지)
                String[] docLines = doc.split("\n");
                for (int i = 0; i < docLines.length; i++) {
                    String dl = docLines[i].trim();
                    if (dl.startsWith("*")) dl = dl.substring(1).trim();
                    if (dl.length() > 0 && !dl.startsWith("@")) {
                        if (dl.length() > 100) dl = dl.substring(0, 100) + "...";
                        return dl;
                    }
                }
            }
        }

        // 2) 파일명 규칙 기반 추론
        String base = fileName.replace(".java", "");
        String[][] keywords = {
            // 비즈니스 도메인
            {"Pay", "결제"}, {"Cash", "캐시/충전"}, {"Cart", "장바구니"},
            {"Board", "게시판"}, {"Notice", "공지사항"}, {"Faq", "FAQ"},
            {"Event", "이벤트"}, {"Inq", "문의"}, {"Cust", "고객/회원"},
            {"User", "사용자"}, {"Member", "회원"}, {"Login", "로그인"},
            {"Auth", "인증/권한"}, {"Session", "세션"},
            {"Order", "주문"}, {"Product", "상품"}, {"Item", "아이템"},
            {"Point", "포인트"}, {"Gpoint", "G포인트"}, {"Coupon", "쿠폰"},
            {"Common", "공통"}, {"Main", "메인"}, {"Index", "인덱스"},
            {"Include", "인클루드/레이아웃"}, {"Turn", "전환"},
            {"Config", "설정"}, {"Batch", "배치"}, {"Schedule", "스케줄"},
            {"Api", "외부 API 연동"}, {"Bithumb", "빗썸 API 연동"},
            {"Breadbear", "브레드베어 API 연동"}, {"Giftyshow", "기프티쇼 API 연동"},
            // 유틸리티/공통
            {"Util", "유틸리티"}, {"Utils", "유틸리티"}, {"Helper", "헬퍼"},
            {"Convert", "데이터 변환"}, {"Cookie", "쿠키 처리"},
            {"Image", "이미지 처리"}, {"File", "파일 처리"},
            {"String", "문자열 처리"}, {"Date", "날짜 처리"},
            {"Sms", "SMS 발송"}, {"Mail", "메일 발송"},
            {"Http", "HTTP 통신"}, {"Client", "클라이언트 통신"},
            // 보안/암호화
            {"Aes", "AES 암호화"}, {"AES", "AES 암호화"},
            {"Cipher", "암복호화"}, {"Security", "보안"},
            {"Encrypt", "암호화"}, {"Decrypt", "복호화"},
            {"XSS", "XSS 필터링"}, {"HTML", "HTML 필터링"},
            {"Filter", "필터"}, {"Wrapper", "요청 래퍼"},
            // 프레임워크/인프라
            {"Interceptor", "인터셉터"}, {"Exception", "예외 처리"},
            {"Handler", "핸들러"}, {"Listener", "리스너"},
            {"Initializer", "초기화"}, {"Renderer", "렌더링"},
            {"Validator", "유효성 검증"}, {"Pagination", "페이징"},
            {"DataTable", "데이터 테이블 처리"},
            {"TagLib", "커스텀 태그 라이브러리"},
            {"Stat", "통계"}, {"Report", "리포트"},
            {"If", "인터페이스/연동"}, {"Test", "테스트"},
        };
        StringBuilder desc = new StringBuilder();
        for (int i = 0; i < keywords.length; i++) {
            if (base.contains(keywords[i][0])) {
                // 중복 방지 (예: AES와 Aes 동시 매칭)
                if (desc.indexOf(keywords[i][1]) < 0) {
                    if (desc.length() > 0) desc.append("/");
                    desc.append(keywords[i][1]);
                }
            }
        }
        // 클래스 유형 접미사
        if (base.endsWith("Controller") || base.endsWith("Action")) {
            desc.append(" 컨트롤러");
        } else if (base.endsWith("ServiceImpl")) {
            desc.append(" 서비스 구현체");
        } else if (base.endsWith("Service")) {
            desc.append(" 서비스 인터페이스");
        } else if (base.endsWith("Dao") || base.endsWith("DAO") || base.endsWith("Mapper")) {
            desc.append(" 데이터 접근");
        } else if (base.endsWith("Vo") || base.endsWith("VO") || base.endsWith("Dto") || base.endsWith("DTO")) {
            desc.append(" 데이터 객체");
        }

        // 3) 패키지명에서 추가 컨텍스트 추출
        int pkgIdx = content.indexOf("package ");
        if (pkgIdx >= 0) {
            int pkgEnd = content.indexOf(';', pkgIdx);
            if (pkgEnd > pkgIdx) {
                String pkg = content.substring(pkgIdx + 8, pkgEnd).trim();
                // 패키지명에서 도메인 힌트 추출 (예: gpoint.pay → 결제 관련)
                if (desc.length() == 0) {
                    String[] parts = pkg.split("\\.");
                    for (int i = 0; i < parts.length; i++) {
                        for (int j = 0; j < keywords.length; j++) {
                            if (parts[i].toLowerCase().contains(keywords[j][0].toLowerCase())
                                    && desc.indexOf(keywords[j][1]) < 0) {
                                if (desc.length() > 0) desc.append("/");
                                desc.append(keywords[j][1]);
                            }
                        }
                    }
                    if (desc.length() > 0) desc.append(" 관련 클래스");
                }
            }
        }

        // 4) RequestMapping URL 추출
        int rmIdx = content.indexOf("@RequestMapping");
        if (rmIdx >= 0) {
            int qIdx = content.indexOf('"', rmIdx);
            if (qIdx >= 0 && qIdx < rmIdx + 80) {
                int qEnd = content.indexOf('"', qIdx + 1);
                if (qEnd > qIdx) {
                    String url = content.substring(qIdx + 1, qEnd);
                    desc.append(" [").append(url).append("]");
                }
            }
        }

        return desc.toString();
    }

    /** 매퍼 XML 파일명에서 비즈니스 기능 추론 */
    private String guessMapperDescription(String fileName) {
        String base = fileName.replace("_SQL.xml", "").replace("Mapper.xml", "")
                .replace("_sql.xml", "").replace(".xml", "");
        // mybatis_sql_www_board → board 추출
        if (base.startsWith("mybatis_sql_")) {
            base = base.substring(base.lastIndexOf('_') + 1);
        }
        String[][] keywords = {
            {"pay", "결제"}, {"cash", "캐시"}, {"cart", "장바구니"},
            {"board", "게시판"}, {"notice", "공지"}, {"faq", "FAQ"},
            {"event", "이벤트"}, {"inq", "문의"}, {"cust", "고객"},
            {"user", "사용자"}, {"order", "주문"}, {"product", "상품"},
            {"point", "포인트"}, {"gpoint", "G포인트"}, {"coupon", "쿠폰"},
            {"common", "공통"}, {"main", "메인"}, {"stat", "통계"},
            {"turn", "전환"}, {"www", "웹"}, {"api", "API"},
        };
        StringBuilder desc = new StringBuilder();
        String lower = base.toLowerCase();
        for (int i = 0; i < keywords.length; i++) {
            if (lower.contains(keywords[i][0])) {
                if (desc.length() > 0) desc.append("/");
                desc.append(keywords[i][1]);
            }
        }
        if (desc.length() > 0) desc.append(" SQL 매퍼");
        return desc.toString();
    }

    private boolean isClassDeclaration(String line) {
        if (line.contains("new ") || line.endsWith(";")) return false;
        return (line.startsWith("public ") || line.startsWith("abstract ")
                || line.startsWith("final ") || line.startsWith("protected "))
                && (line.contains(" class ") || line.contains(" interface ") || line.contains(" enum "));
    }

    private boolean isMethodSignature(String line) {
        if (!line.contains("(")) return false;
        if (line.startsWith("if") || line.startsWith("for") || line.startsWith("while")
                || line.startsWith("switch") || line.startsWith("catch") || line.startsWith("try")
                || line.startsWith("new ") || line.startsWith("return ")
                || line.startsWith("super") || line.startsWith("this(")) return false;
        if (line.contains("= new ") || line.contains(".get(") || line.contains(".set(")
                || line.contains(".put(") || line.contains(".add(")
                || line.contains(".append(")) return false;
        return (line.startsWith("public ") || line.startsWith("protected ")
                || line.startsWith("private ") || line.startsWith("abstract ")
                || line.startsWith("static "))
                && (line.endsWith("{") || line.endsWith(";") || line.endsWith(")")
                    || line.contains(") {") || line.contains(") throws"));
    }

    private boolean isFieldDeclaration(String line) {
        return (line.startsWith("private ") || line.startsWith("protected ")
                || line.startsWith("public "))
                && line.endsWith(";") && !line.contains("(")
                && !line.contains(" static final ");
    }

    private void appendMapperInfo(StringBuilder sb, File f, File projectDir) {
        String content = readFileContent(f, 200000);
        if (content.length() == 0) return;
        sb.append("### ").append(f.getName()).append("\n");
        sb.append("\uacbd\ub85c: ").append(relativePath(f, projectDir)).append("\n");

        // 매퍼 설명 추론
        String mapperDesc = guessMapperDescription(f.getName());
        if (mapperDesc.length() > 0) {
            sb.append("\uc124\uba85: ").append(mapperDesc).append("\n");
        }

        int nsIdx = content.indexOf("namespace=\"");
        if (nsIdx < 0) nsIdx = content.indexOf("namespace='");
        if (nsIdx >= 0) {
            int start = nsIdx + 11;
            char quote = content.charAt(nsIdx + 10);
            int end = content.indexOf(quote, start);
            if (end > start) {
                sb.append("namespace: ").append(content.substring(start, end)).append("\n");
            }
        }

        String[] tags = {"select", "insert", "update", "delete"};
        for (int t = 0; t < tags.length; t++) {
            String tag = tags[t];
            String pattern = "<" + tag;
            int pos = 0;
            while ((pos = content.indexOf(pattern, pos)) >= 0) {
                int nextChar = pos + pattern.length();
                if (nextChar < content.length()) {
                    char c = content.charAt(nextChar);
                    if (c != ' ' && c != '\t' && c != '\n' && c != '>') {
                        pos = nextChar;
                        continue;
                    }
                }
                int endTag = content.indexOf(">", pos);
                if (endTag < 0) break;
                String tagText = content.substring(pos, endTag);

                int idIdx = tagText.indexOf("id=\"");
                if (idIdx < 0) idIdx = tagText.indexOf("id='");
                if (idIdx >= 0) {
                    int idStart = idIdx + 4;
                    char q = tagText.charAt(idIdx + 3);
                    int idEnd = tagText.indexOf(q, idStart);
                    if (idEnd > idStart) {
                        sb.append("  ").append(tag.toUpperCase()).append(": ")
                          .append(tagText.substring(idStart, idEnd));

                        int ptIdx = tagText.indexOf("parameterType=\"");
                        if (ptIdx >= 0) {
                            int ptStart = ptIdx + 15;
                            int ptEnd = tagText.indexOf('"', ptStart);
                            if (ptEnd > ptStart) {
                                String pt = tagText.substring(ptStart, ptEnd);
                                int lastDot = pt.lastIndexOf('.');
                                sb.append(" (param: ").append(lastDot >= 0 ? pt.substring(lastDot + 1) : pt).append(")");
                            }
                        }

                        int rtIdx = tagText.indexOf("resultType=\"");
                        if (rtIdx < 0) rtIdx = tagText.indexOf("resultMap=\"");
                        if (rtIdx >= 0) {
                            boolean isType = tagText.charAt(rtIdx + 6) == 'T';
                            int rtStart = rtIdx + (isType ? 12 : 11);
                            int rtEnd = tagText.indexOf('"', rtStart);
                            if (rtEnd > rtStart) {
                                String rt = tagText.substring(rtStart, rtEnd);
                                int lastDot = rt.lastIndexOf('.');
                                sb.append(" -> ").append(lastDot >= 0 ? rt.substring(lastDot + 1) : rt);
                            }
                        }
                        sb.append("\n");
                    }
                }
                pos = endTag;
            }
        }
        sb.append("\n");
    }

    // ── 파일 유틸리티 ──

    private void findAllFiles(File dir, String suffix, List result, int depth, int maxFiles) {
        if (depth > 10 || dir == null || !dir.isDirectory()) return;
        if (result.size() >= maxFiles) return;
        File[] children = dir.listFiles();
        if (children == null) return;
        for (int i = 0; i < children.length; i++) {
            if (result.size() >= maxFiles) return;
            File child = children[i];
            String name = child.getName();
            if (child.isDirectory()) {
                if (name.startsWith(".") || "target".equals(name) || "build".equals(name)
                        || "node_modules".equals(name) || "bin".equals(name)
                        || "test-output".equals(name)) continue;
                findAllFiles(child, suffix, result, depth + 1, maxFiles);
            } else if (name.endsWith(suffix)) {
                result.add(child);
            }
        }
    }

    private String buildProfileTree(File dir, String indent, int depth) {
        if (depth > 6 || dir == null || !dir.isDirectory()) return "";
        StringBuilder sb = new StringBuilder();
        File[] children = dir.listFiles();
        if (children == null) return "";
        java.util.Arrays.sort(children, new java.util.Comparator() {
            public int compare(Object a, Object b) {
                File fa = (File) a, fb = (File) b;
                if (fa.isDirectory() != fb.isDirectory()) return fa.isDirectory() ? -1 : 1;
                return fa.getName().compareToIgnoreCase(fb.getName());
            }
        });
        for (int i = 0; i < children.length; i++) {
            String name = children[i].getName();
            if (name.startsWith(".") || "target".equals(name) || "build".equals(name)
                    || "node_modules".equals(name) || "bin".equals(name)) continue;
            if (children[i].isDirectory()) {
                sb.append(indent).append(name).append("/\n");
                sb.append(buildProfileTree(children[i], indent + "  ", depth + 1));
            } else {
                if (name.endsWith(".class") || name.endsWith(".jar") || name.endsWith(".war")) continue;
                // 이미지/폰트/바이너리 제외
                if (name.endsWith(".png") || name.endsWith(".jpg") || name.endsWith(".jpeg")
                        || name.endsWith(".gif") || name.endsWith(".ico") || name.endsWith(".bmp")
                        || name.endsWith(".svg") || name.endsWith(".woff") || name.endsWith(".woff2")
                        || name.endsWith(".ttf") || name.endsWith(".eot") || name.endsWith(".mp3")
                        || name.endsWith(".mp4") || name.endsWith(".zip") || name.endsWith(".gz")
                        || name.endsWith(".pdf") || name.endsWith(".hwp") || name.endsWith(".doc")
                        || name.endsWith(".xls") || name.endsWith(".pptx")) continue;
                sb.append(indent).append(name).append("\n");
            }
            if (sb.length() > 20000) {
                sb.append(indent).append("...(\uc774\ud558 \uc0dd\ub7b5)\n");
                break;
            }
        }
        return sb.toString();
    }

    private void appendConfigSection(StringBuilder sb, File dir, String name, int maxLen) {
        File f = new File(dir, name);
        if (f.exists() && f.isFile()) {
            sb.append("### ").append(name).append("\n```\n");
            sb.append(readFileContent(f, maxLen));
            sb.append("\n```\n\n");
        }
    }

    private File findFileDeep(File dir, String name, int maxDepth) {
        if (maxDepth <= 0 || dir == null || !dir.isDirectory()) return null;
        File direct = new File(dir, name);
        if (direct.exists() && direct.isFile()) return direct;
        File[] children = dir.listFiles();
        if (children == null) return null;
        for (int i = 0; i < children.length; i++) {
            if (children[i].isDirectory() && !children[i].getName().startsWith(".")
                    && !"target".equals(children[i].getName())
                    && !"build".equals(children[i].getName())) {
                File r = findFileDeep(children[i], name, maxDepth - 1);
                if (r != null) return r;
            }
        }
        return null;
    }

    private String relativePath(File file, File projectDir) {
        String base = projectDir.getAbsolutePath();
        String path = file.getAbsolutePath();
        if (path.startsWith(base)) {
            return path.substring(base.length() + 1).replace('\\', '/');
        }
        return file.getName();
    }

    /** 프로필 서버 업로드용 소스 파일 수집 (Java, XML, JSP) */
    private java.util.List collectSourceFilesForUpload(File projectDir) {
        java.util.List result = new ArrayList();
        if (projectDir == null || !projectDir.isDirectory()) return result;
        final int MAX_PER_FILE = 80000;
        java.util.List javaFiles = new ArrayList();
        java.util.List mapperXmls = new ArrayList();
        java.util.List allXmls = new ArrayList();
        java.util.List jspFiles = new ArrayList();
        findAllFiles(projectDir, ".java", javaFiles, 0, 120);
        findAllFiles(projectDir, "Mapper.xml", mapperXmls, 0, 50);
        findAllFiles(projectDir, "_SQL.xml", mapperXmls, 0, 50);
        findAllFiles(projectDir, "_sql.xml", mapperXmls, 0, 50);
        findAllFiles(projectDir, ".xml", allXmls, 0, 200);
        for (int i = 0; i < allXmls.size() && mapperXmls.size() < 40; i++) {
            File xf = (File) allXmls.get(i);
            String xfName = xf.getName().toLowerCase();
            if ((xfName.startsWith("mybatis") || xfName.contains("_sql_"))
                    && !mapperXmls.contains(xf)) {
                mapperXmls.add(xf);
            }
        }
        findAllFiles(projectDir, ".jsp", jspFiles, 0, 30);
        for (int i = 0; i < javaFiles.size(); i++) {
            File f = (File) javaFiles.get(i);
            String content = readFileContent(f, 500000);
            if (content.length() > MAX_PER_FILE) content = content.substring(0, MAX_PER_FILE);
            java.util.Map m = new java.util.HashMap();
            m.put("path", relativePath(f, projectDir));
            m.put("content", content);
            result.add(m);
        }
        for (int i = 0; i < mapperXmls.size(); i++) {
            File f = (File) mapperXmls.get(i);
            String content = readFileContent(f, 500000);
            if (content.length() > MAX_PER_FILE) content = content.substring(0, MAX_PER_FILE);
            java.util.Map m = new java.util.HashMap();
            m.put("path", relativePath(f, projectDir));
            m.put("content", content);
            result.add(m);
        }
        for (int i = 0; i < jspFiles.size(); i++) {
            File f = (File) jspFiles.get(i);
            String content = readFileContent(f, 500000);
            if (content.length() > MAX_PER_FILE) content = content.substring(0, MAX_PER_FILE);
            java.util.Map m = new java.util.HashMap();
            m.put("path", relativePath(f, projectDir));
            m.put("content", content);
            result.add(m);
        }
        return result;
    }

    private boolean saveTextFile(File f, String content) {
        try {
            java.io.OutputStreamWriter w = new java.io.OutputStreamWriter(
                    new java.io.FileOutputStream(f), FILE_UTF8);
            w.write(content);
            w.close();
            return true;
        } catch (Exception e) {
            System.err.println("[Nori] saveTextFile 실패: " + f.getAbsolutePath() + " — " + e.getMessage());
            return false;
        }
    }

    private String readFileContent(File f, int maxLen) {
        try {
            BufferedReader br = new BufferedReader(
                    new InputStreamReader(new FileInputStream(f), FILE_UTF8));
            StringBuilder sb = new StringBuilder();
            char[] buf = new char[8192];
            int n;
            while ((n = br.read(buf)) != -1) {
                sb.append(buf, 0, n);
            }
            br.close();
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /** 코드 블록을 클립보드에 복사 */
    private void copyCodeToClipboard(int blockIndex) {
        if (blockIndex < 0 || blockIndex >= codeBlocks.size()) return;
        String code = (String) codeBlocks.get(blockIndex);
        try {
            org.eclipse.swt.dnd.Clipboard cb = new org.eclipse.swt.dnd.Clipboard(Display.getDefault());
            cb.setContents(new Object[]{code}, new org.eclipse.swt.dnd.Transfer[]{
                org.eclipse.swt.dnd.TextTransfer.getInstance()
            });
            cb.dispose();
            messages.add(new String[]{"system", "\u2705 코드가 클립보드에 복사되었습니다.", null});
        } catch (Exception e) {
            messages.add(new String[]{"system", "\u274C 복사 실패: " + e.getMessage(), null});
        }
        refreshDisplay();
    }

    /* ═══════════════════════════════════════════════════════
     *  코드 적용 — AI 응답의 코드 블록을 에디터에 삽입/교체
     * ═══════════════════════════════════════════════════════ */

    private void applyCode(int blockIndex) {
        if (blockIndex < 0 || blockIndex >= codeBlocks.size()) return;
        String code = (String) codeBlocks.get(blockIndex);

        try {
            IWorkbenchPage page = PlatformUI.getWorkbench()
                    .getActiveWorkbenchWindow().getActivePage();
            if (page == null) return;
            IEditorPart editor = page.getActiveEditor();
            if (editor == null) return;

            ITextEditor textEditor = null;
            if (editor instanceof ITextEditor) {
                textEditor = (ITextEditor) editor;
            } else {
                Object adapted = editor.getAdapter(ITextEditor.class);
                if (adapted instanceof ITextEditor) {
                    textEditor = (ITextEditor) adapted;
                }
            }
            if (textEditor == null) return;

            IDocument doc = textEditor.getDocumentProvider()
                    .getDocument(textEditor.getEditorInput());
            if (doc == null) return;

            ISelection sel = textEditor.getSelectionProvider().getSelection();
            if (sel instanceof ITextSelection) {
                ITextSelection textSel = (ITextSelection) sel;
                if (textSel.getLength() > 0) {
                    doc.replace(textSel.getOffset(), textSel.getLength(), code);
                } else {
                    doc.replace(textSel.getOffset(), 0, code);
                }
            }

            messages.add(new String[]{"system",
                    "\u2705 코드가 에디터에 적용되었습니다.", null});
            refreshDisplay();
        } catch (Exception e) {
            messages.add(new String[]{"system",
                    "\u274C 코드 적용 실패: " + e.getMessage(), null});
            refreshDisplay();
        }
    }

    /* ═══════════════════════════════════════════════════════
     *  테스트 실행 — AI 생성 테스트 코드를 저장 + Maven 실행
     * ═══════════════════════════════════════════════════════ */

    private void executeTest(int blockIndex) {
        if (blockIndex < 0 || blockIndex >= codeBlocks.size()) return;
        final String code = (String) codeBlocks.get(blockIndex);

        final File projectDir = getActiveProjectDir();
        if (projectDir == null) {
            messages.add(new String[]{"system",
                    "\u274C 프로젝트를 찾을 수 없습니다.", null});
            refreshDisplay();
            return;
        }

        // 클래스명 추출
        final String className = extractTestClassName(code);
        if (className == null) {
            messages.add(new String[]{"system",
                    "\u274C 테스트 클래스명을 찾을 수 없습니다.", null});
            refreshDisplay();
            return;
        }

        // 패키지명 추출
        String packageName = extractPackageName(code);

        // 테스트 디렉토리 생성 + 저장
        File testDir = new File(projectDir, "src/test/java");
        if (packageName != null && packageName.length() > 0) {
            testDir = new File(testDir, packageName.replace('.', File.separatorChar));
        }
        testDir.mkdirs();

        final File testFile = new File(testDir, className + ".java");
        saveTextFile(testFile, code);

        messages.add(new String[]{"system",
                "\uD83D\uDCBE 테스트 저장: " + relativePath(testFile, projectDir), null});

        // 라이브러리 의존성 체크
        checkTestDependencies(projectDir, code);

        refreshDisplay();

        // Maven 테스트 실행
        Job job = new Job("Nori AI - 테스트 실행") {
            protected IStatus run(IProgressMonitor monitor) {
                addStatusOnUI("\u25B6 테스트 실행 중: " + className);
                try {
                    ProcessBuilder pb = new ProcessBuilder();
                    pb.directory(projectDir);
                    pb.redirectErrorStream(true);

                    // mvnw 또는 mvn 사용
                    File mvnw = new File(projectDir, "mvnw.cmd");
                    if (!mvnw.exists()) mvnw = new File(projectDir, "mvnw");
                    String mvnCmd = mvnw.exists() ? mvnw.getAbsolutePath() : "mvn";

                    pb.command(mvnCmd, "test", "-Dtest=" + className, "-pl", ".");

                    Process process = pb.start();
                    BufferedReader reader = new BufferedReader(
                            new InputStreamReader(process.getInputStream(), FILE_UTF8));
                    final StringBuilder output = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) {
                        output.append(line).append("\n");
                    }
                    reader.close();
                    int exitCode = process.waitFor();

                    final String result;
                    if (exitCode == 0) {
                        result = "\u2705 테스트 성공!\n```\n" + output.toString() + "```";
                    } else {
                        result = "\u274C 테스트 실패 (exit: " + exitCode + ")\n```\n"
                                + output.toString() + "```";
                    }

                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            messages.add(new String[]{"system", result, null});
                            refreshDisplay();
                        }
                    });
                } catch (final Exception e) {
                    Display.getDefault().asyncExec(new Runnable() {
                        public void run() {
                            messages.add(new String[]{"system",
                                    "\u274C 테스트 실행 오류: " + e.getMessage()
                                    + "\n\uD83D\uDCA1 Maven이 PATH에 있는지 확인하세요.", null});
                            refreshDisplay();
                        }
                    });
                }
                return Status.OK_STATUS;
            }
        };
        job.setUser(false);
        job.schedule();
    }

    private String extractTestClassName(String code) {
        int classIdx = code.indexOf(" class ");
        if (classIdx < 0) return null;
        int nameStart = classIdx + 7;
        int nameEnd = nameStart;
        while (nameEnd < code.length() && code.charAt(nameEnd) != ' '
                && code.charAt(nameEnd) != '{' && code.charAt(nameEnd) != '<'
                && code.charAt(nameEnd) != '\n') {
            nameEnd++;
        }
        if (nameEnd > nameStart) {
            return code.substring(nameStart, nameEnd).trim();
        }
        return null;
    }

    private String extractPackageName(String code) {
        int pkgIdx = code.indexOf("package ");
        if (pkgIdx < 0) return null;
        int start = pkgIdx + 8;
        int end = code.indexOf(';', start);
        if (end < 0) return null;
        return code.substring(start, end).trim();
    }

    /**
     * 테스트 코드에 필요한 라이브러리(JUnit5, Mockito 등)가 프로젝트에 있는지 확인하고
     * 없으면 안내 메시지를 UI에 추가한다.
     */
    private void checkTestDependencies(File projectDir, String code) {
        // 필요한 라이브러리 판별
        boolean needJunit5 = code.contains("org.junit.jupiter")
                || code.contains("@ExtendWith") || code.contains("@DisplayName")
                || code.contains("import org.junit.jupiter");
        boolean needMockito = code.contains("org.mockito")
                || code.contains("@Mock") || code.contains("@InjectMocks");
        boolean needSpringTest = code.contains("@SpringBootTest")
                || code.contains("@WebMvcTest") || code.contains("MockMvc");

        if (!needJunit5 && !needMockito && !needSpringTest) return;

        // pom.xml 확인
        File pomFile = new File(projectDir, "pom.xml");
        String pomContent = "";
        if (pomFile.exists()) {
            try {
                pomContent = readFileToString(pomFile);
            } catch (Exception e) { /* ignore */ }
        }

        StringBuilder guide = new StringBuilder();
        guide.append("\uD83D\uDCE6 <b>\uc758\uc874\uc131 \ud655\uc778</b><br>");

        boolean hasMissing = false;

        if (needJunit5 && !pomContent.contains("junit-jupiter")) {
            hasMissing = true;
            guide.append("<br>\u274C <b>JUnit 5</b> \ubbf8\ubc1c\uacac<br>");
            guide.append("<code>&lt;dependency&gt;<br>");
            guide.append("  &lt;groupId&gt;org.junit.jupiter&lt;/groupId&gt;<br>");
            guide.append("  &lt;artifactId&gt;junit-jupiter&lt;/artifactId&gt;<br>");
            guide.append("  &lt;version&gt;5.9.3&lt;/version&gt;<br>");
            guide.append("  &lt;scope&gt;test&lt;/scope&gt;<br>");
            guide.append("&lt;/dependency&gt;</code><br>");
        }
        if (needMockito && !pomContent.contains("mockito-core")
                && !pomContent.contains("mockito-junit-jupiter")) {
            hasMissing = true;
            guide.append("<br>\u274C <b>Mockito</b> \ubbf8\ubc1c\uacac<br>");
            guide.append("<code>&lt;dependency&gt;<br>");
            guide.append("  &lt;groupId&gt;org.mockito&lt;/groupId&gt;<br>");
            guide.append("  &lt;artifactId&gt;mockito-junit-jupiter&lt;/artifactId&gt;<br>");
            guide.append("  &lt;version&gt;5.3.1&lt;/version&gt;<br>");
            guide.append("  &lt;scope&gt;test&lt;/scope&gt;<br>");
            guide.append("&lt;/dependency&gt;</code><br>");
        }
        if (needSpringTest && !pomContent.contains("spring-boot-starter-test")) {
            hasMissing = true;
            guide.append("<br>\u274C <b>Spring Boot Test</b> \ubbf8\ubc1c\uacac<br>");
            guide.append("<code>&lt;dependency&gt;<br>");
            guide.append("  &lt;groupId&gt;org.springframework.boot&lt;/groupId&gt;<br>");
            guide.append("  &lt;artifactId&gt;spring-boot-starter-test&lt;/artifactId&gt;<br>");
            guide.append("  &lt;scope&gt;test&lt;/scope&gt;<br>");
            guide.append("&lt;/dependency&gt;</code><br>");
        }

        if (!hasMissing) {
            guide.append("\u2705 \ud544\uc694\ud55c \ub77c\uc774\ube0c\ub7ec\ub9ac\uac00 \ubaa8\ub450 \uc788\uc2b5\ub2c8\ub2e4.");
            messages.add(new String[]{"dep-guide", guide.toString(), null});
            return;
        }

        if (pomFile.exists()) {
            guide.append("<br>\uD83D\uDCA1 \uc704 \ub0b4\uc6a9\uc744 <b>pom.xml</b>\uc758 &lt;dependencies&gt; \uc548\uc5d0 \ucd94\uac00\ud558\uc138\uc694.");
        }

        // 네트워크 차단 시 lib 폴더 jar 안내
        guide.append("<br><br>\uD83D\uDD12 <b>\ub124\ud2b8\uc6cc\ud06c \ucc28\ub2e8 \uc2dc</b>: ");
        File libDir = new File(projectDir, "src/main/webapp/WEB-INF/lib");
        if (!libDir.exists()) {
            libDir = new File(projectDir, "lib");
        }
        guide.append("JAR \ud30c\uc77c\uc744 <b>").append(libDir.getAbsolutePath())
             .append("</b> \ud3f4\ub354\uc5d0 \uc9c1\uc811 \ubcf5\uc0ac\ud558\uc138\uc694.");

        messages.add(new String[]{"dep-guide", guide.toString(), null});
    }

    private String readFileToString(File file) throws Exception {
        java.io.FileInputStream fis = null;
        try {
            fis = new java.io.FileInputStream(file);
            byte[] data = new byte[(int) file.length()];
            fis.read(data);
            return new String(data, "UTF-8");
        } finally {
            if (fis != null) try { fis.close(); } catch (Exception e) {}
        }
    }

    /* ═══════════════════════════════════════════════════════
     *  화면 렌더링
     * ═══════════════════════════════════════════════════════ */

    private void refreshDisplay() {
        if (useBrowser) {
            if (browser == null || browser.isDisposed()) return;
            codeBlocks.clear();
            browser.setText(buildHtml());
        } else {
            if (fallbackText == null || fallbackText.isDisposed()) return;
            fallbackText.setText(buildPlainText());
            fallbackText.setTopIndex(fallbackText.getLineCount() - 1);
        }
    }

    /* ── HTML 빌드 ── */

    private static final String CSS = ""; /* nori-chat.css 외부 파일로 이동 */

    private String buildHtml() {
        StringBuilder sb = new StringBuilder();

        // ── 상단 버튼 바: 채팅 목록 + 새 채팅 ──
        sb.append("<div class='nori-context-bar'>");
        sb.append("<button class='ctx-btn' onclick='toggleChatListPanel()' title='\ucc44\ud305 \ubaa9\ub85d'>\uD83D\uDCCB \ucc44\ud305 \ubaa9\ub85d</button>");
        sb.append("<button class='ctx-btn ctx-new' onclick='startNewChatFromJs()' title='\uc0c8 \ucc44\ud305'>\u2795 \uc0c8 \ucc44\ud305</button>");
        sb.append("</div>");

        // \ud504\ub85c\ud544 \uc0c1\ud0dc \uc548\ub0b4 (\uac04\uc18c\ud654: \uc0c1\ub2e8 1\uc904)
        if (profileState == 0) {
            sb.append("<div class='nori-banner'>\u23F3 \ud504\ub85c\uc81d\ud2b8 \ubd84\uc11d \ub300\uae30 \uc911\u2026</div>");
        } else if (profileState == 1) {
            sb.append("<div class='nori-banner'>\u23F3 \ud504\ub85c\uc81d\ud2b8 \ubd84\uc11d \uc911\u2026</div>");
        } else {
            sb.append("<div class='nori-banner done'>\u2705 \ubd84\uc11d \uc644\ub8cc &mdash; \uc7ac\ubd84\uc11d: \uc6b0\ud074\ub9ad \u2192 Nori AI \u2192 \ud504\ub85c\uc81d\ud2b8 \ubd84\uc11d</div>");
        }

        // \uc9c8\ubb38/\ub2f5\ubcc0\ub9cc \ub80c\ub354\ub9c1 (step, system, thinking \ub4f1 \ubaa8\ub450 \uc0dd\ub7b5)
        for (int i = 0; i < messages.size(); i++) {
            String[] msg = (String[]) messages.get(i);
            String role = msg[0];
            String content = msg[1];

            if ("user".equals(role)) {
                sb.append("<div class='user-msg'>")
                  .append(escapeHtml(content).replace("\n", "<br>"))
                  .append("</div>");
            } else if ("assistant".equals(role)) {
                sb.append("<div class='nori-response'>");
                sb.append(renderContentWithCodeButtons(content));
                sb.append("</div>");
            }
            // step, step-done, step-fail, system, thinking, dep-guide, pl-card \ubaa8\ub450 \uc0dd\ub7b5
        }

        return "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
             + "<style>" + CSS + "</style>"
             + "<style>" + loadChatCss() + "</style>"
             + "<style>" + loadHighlightCss() + "</style>"
             + "</head><body><div class='chat-wrap'>"
             + sb.toString()
             + "</div>"
             + (codeBlocks.isEmpty() ? "" : "<script>" + loadHighlightJs() + "</script>")
             + "<script>window.scrollTo(0,document.body.scrollHeight);</script>"
             + "</body></html>";
    }

    /**
     * 텍스트에서 ```코드 블록```을 파싱, [코드 적용] 버튼 생성.
     * 코드 블록은 codeBlocks 리스트에 저장, 버튼은 인덱스로 참조.
     */
    private String renderContentWithCodeButtons(String content) {
        if (content == null) return "";
        StringBuilder sb = new StringBuilder();
        int pos = 0;
        while (pos < content.length()) {
            int codeStart = content.indexOf("```", pos);
            if (codeStart < 0) {
                String tail = content.substring(pos).replaceAll("<!-- NORI_FILE:[^>]*-->", "").replaceAll("\\n{3,}", "\n\n").trim();
                if (tail.length() > 0) sb.append(renderTextWithFileLinks(tail));
                break;
            }

            String textBefore = content.substring(pos, codeStart);
            String filePath = null;
            int startLine = 1;
            // 마지막 NORI_FILE 사용 (여러 턴이 있을 때 코드블록 직전 마커가 해당 파일)
            int markerStart = textBefore.lastIndexOf("<!-- NORI_FILE:");
            if (markerStart >= 0) {
                int markerEnd = textBefore.indexOf("-->", markerStart);
                if (markerEnd >= 0) {
                    String marker = textBefore.substring(markerStart + 15, markerEnd).trim();
                    int bar = marker.indexOf('|');
                    if (bar >= 0) {
                        String pPart = marker.substring(0, bar).trim();
                        if (pPart.startsWith("path=")) filePath = pPart.substring(5).trim();
                        String lPart = marker.substring(bar + 1).trim();
                        if (lPart.startsWith("line=")) lPart = lPart.substring(5);
                        try { startLine = Integer.parseInt(lPart); } catch (NumberFormatException e) { }
                    }
                    textBefore = (textBefore.substring(0, markerStart) + textBefore.substring(markerEnd + 3)).trim();
                }
            }
            // NORI_FILE 마커 전부 제거 (화면에 raw로 보이지 않게)
            textBefore = textBefore.replaceAll("<!-- NORI_FILE:[^>]*-->", "").replaceAll("\\n{3,}", "\n\n").trim();
            if (codeStart > pos && textBefore.length() > 0) {
                sb.append(renderTextWithFileLinks(textBefore));
            }

            int lineEnd = content.indexOf('\n', codeStart);
            if (lineEnd < 0) lineEnd = content.length();
            int codeContentStart = lineEnd + 1;

            int codeEnd = content.indexOf("```", codeContentStart);
            if (codeEnd < 0) codeEnd = content.length();

            // 코드 언어 추출 (```java, ```xml 등)
            String langLine = content.substring(codeStart + 3, lineEnd).trim().toLowerCase();
            String lang = "";
            if (langLine.length() > 0 && langLine.matches("[a-z0-9]+")) {
                lang = langLine;
            }

            String codeText = content.substring(codeContentStart, codeEnd).trim();
            int blockIdx = codeBlocks.size();
            codeBlocks.add(codeText);

            String codeEscaped = escapeHtml(codeText);
            codeEscaped = codeEscaped.replace("// \u2605 \uCD94\uAC00", "<span style='color:#4ec9b0'>// \u2605 \uCD94\uAC00</span>");
            codeEscaped = codeEscaped.replace("// \u2605 \uC218\uC815", "<span style='color:#dcdcaa'>// \u2605 \uC218\uC815</span>");

            if (filePath != null && filePath.length() > 0) {
                String fname = filePath;
                int slashIdx = filePath.lastIndexOf('/');
                if (slashIdx >= 0) fname = filePath.substring(slashIdx + 1);
                String safePath = escapeHtml(filePath).replace("'", "\\'");
                sb.append("<div class='nori-source-card' data-file-path='")
                  .append(escapeHtml(filePath)).append("' data-start-line='").append(startLine).append("'>");
                sb.append("<div class='nori-file-info'>");
                sb.append("<div class='file-name'>\uD83D\uDCC4 ").append(escapeHtml(fname)).append("</div>");
                sb.append("<a class='file-path' href=\"#\" onclick=\"openFileInProject('")
                  .append(safePath).append("',").append(startLine).append(");return false;\">");
                sb.append("\uD83D\uDCC2 ").append(escapeHtml(filePath)).append("</a>");
                sb.append("<div class='file-line'>\uD83D\uDCCD \uC218\uC815 \uB77C\uC778: ").append(startLine).append("\uBC88 \uC904</div>");
                sb.append("</div>");
                if (lang.length() > 0) {
                    sb.append("<div class='code-lang-tag'>").append(escapeHtml(lang)).append("</div>");
                }
                sb.append("<div class='nori-source-box'><pre><code class='language-").append(lang).append("'>");
                sb.append(codeEscaped);
                sb.append("</code></pre></div>");
                sb.append("<div class='nori-source-actions'>");
                sb.append("<div class='action-left'>");
                sb.append("<button class='action-btn' onclick='likeStreamFile(this)' title='\uC88B\uC544\uC694'>\uD83D\uDC4D</button>");
                sb.append("<button class='action-btn' onclick='dislikeStreamFile(this)' title='\uC548\uC88B\uC544\uC694'>\uD83D\uDC4E</button>");
                sb.append("</div>");
                sb.append("<div class='action-right'>");
                sb.append("<button class='copy-btn' onclick='copySourceFromCard(this)' title='\uC18C\uC2A4 \uBCF5\uC0AC'>");
                sb.append("<span class='copy-icon'>\uD83D\uDCCB</span></button>");
                sb.append("</div></div></div>");
            } else {
                if (lang.length() > 0) {
                    sb.append("<div class='code-lang-tag'>").append(escapeHtml(lang)).append("</div>");
                }
                sb.append("<pre><code class='language-").append(lang).append("'>");
                sb.append(codeEscaped);
                sb.append("</code></pre>");
                sb.append("<button class='copy-code-btn' onclick='copyCodeBlock(")
                  .append(blockIdx).append(")' title='\uCF54\uB4DC \uBCF5\uC0AC'>\uD83D\uDCCB \uCF54\uB4DC \uBCF5\uC0AC</button>");
                if (codeText.contains("@Test")) {
                    sb.append(" <button class='apply-btn' style='background:#2ea043;' onclick='runTestCode(")
                      .append(blockIdx).append(")'>\u25B6 \uD14C\uC2A4\uD2B8 \uC2E4\uD589</button>");
                }
            }

            pos = codeEnd + 3;
        }
        return sb.toString();
    }

    /**
     * 일반 텍스트에서 파일 경로 패턴을 클릭 가능한 링크로 변환한다.
     * 인식 패턴:
     *   📁 `파일경로` — 설명
     *   `src/main/java/...` (백틱 안의 .java/.xml/.jsp/.properties 경로)
     *   **src/main/java/...** (볼드 안의 파일 경로)
     *   src/main/java/.../File.java (일반 텍스트 파일 경로)
     */
    private String renderTextWithFileLinks(String text) {
        // 전처리: **path** 볼드 형식의 파일 경로를 `path` 백틱 형식으로 변환
        text = convertBoldFilePaths(text);
        String escaped = escapeHtml(text).replace("\n", "<br>");
        // 1) 📁 `파일경로` 패턴 → 클릭 가능한 링크로 변환
        // HTML 이스케이프 후이므로 백틱은 그대로 `
        StringBuilder result = new StringBuilder();
        String marker = "\uD83D\uDCC1 ";  // 📁 + 공백
        int pos = 0;
        while (pos < escaped.length()) {
            int markerIdx = escaped.indexOf(marker, pos);
            if (markerIdx < 0) {
                // 📁 없으면 백틱 내 경로 패턴 처리
                result.append(convertBacktickPaths(escaped.substring(pos)));
                break;
            }
            // 📁 앞의 텍스트
            result.append(convertBacktickPaths(escaped.substring(pos, markerIdx)));
            // 📁 뒤 백틱 안의 경로 추출
            int tickStart = escaped.indexOf('`', markerIdx + marker.length());
            if (tickStart < 0) {
                result.append(escaped.substring(markerIdx));
                break;
            }
            int tickEnd = escaped.indexOf('`', tickStart + 1);
            if (tickEnd < 0) {
                result.append(escaped.substring(markerIdx));
                break;
            }
            String filePath = escaped.substring(tickStart + 1, tickEnd);
            result.append("\uD83D\uDCC1 <span class='file-link' onclick='openFileInProject(\"")
                  .append(escapeJsString(filePath)).append("\")'>").append(filePath).append("</span>");
            pos = tickEnd + 1;
        }
        return result.toString();
    }

    /**
     * 백틱 안의 파일 확장자(.java, .xml, .jsp, .properties, .yml, .sql) 경로를 클릭 가능한 링크로 변환
     */
    private String convertBacktickPaths(String html) {
        StringBuilder sb = new StringBuilder();
        int pos = 0;
        while (pos < html.length()) {
            int tick1 = html.indexOf('`', pos);
            if (tick1 < 0) {
                sb.append(linkifyPlainPaths(html.substring(pos)));
                break;
            }
            sb.append(linkifyPlainPaths(html.substring(pos, tick1)));
            int tick2 = html.indexOf('`', tick1 + 1);
            if (tick2 < 0) {
                sb.append(linkifyPlainPaths(html.substring(tick1)));
                break;
            }
            String inside = html.substring(tick1 + 1, tick2);
            // 파일 경로 패턴인지 확인: src/ 로 시작하거나 .java/.xml/.jsp 등 확장자
            if (isFilePath(inside)) {
                sb.append("<span class='file-link' onclick='openFileInProject(\"")
                  .append(escapeJsString(inside)).append("\")'>").append(inside).append("</span>");
            } else {
                sb.append("<code>").append(inside).append("</code>");
            }
            pos = tick2 + 1;
        }
        return sb.toString();
    }

    /**
     * 일반 텍스트에서 파일 경로 패턴(src/main/.../File.java)을 클릭 가능한 링크로 변환.
     * HTML 태그 내부는 건너뜀.
     */
    private String linkifyPlainPaths(String text) {
        StringBuilder sb = new StringBuilder();
        int pos = 0;
        while (pos < text.length()) {
            // HTML 태그 건너뛰기
            if (text.charAt(pos) == '<') {
                int tagEnd = text.indexOf('>', pos);
                if (tagEnd >= 0) {
                    sb.append(text.substring(pos, tagEnd + 1));
                    pos = tagEnd + 1;
                    continue;
                }
            }

            // 경로 문자 패턴 시작점 찾기 (영숫자, /, \, ., -, _)
            int pathStart = -1;
            int scanStart = pos;
            for (int i = pos; i < text.length(); i++) {
                char c = text.charAt(i);
                if (c == '<') break;  // HTML 태그 시작
                if (isPathChar(c)) {
                    pathStart = i;
                    break;
                } else {
                    sb.append(c);
                }
            }
            if (pathStart < 0) {
                if (scanStart < text.length() && text.charAt(scanStart) == '<') continue;
                break;
            }

            // 경로 문자 연속 수집
            int pathEnd = pathStart;
            while (pathEnd < text.length() && isPathChar(text.charAt(pathEnd))) {
                pathEnd++;
            }

            String candidate = text.substring(pathStart, pathEnd);
            if (isFilePath(candidate)) {
                sb.append("<span class='file-link' onclick='openFileInProject(\"")
                  .append(escapeJsString(candidate)).append("\")'>").append(candidate).append("</span>");
            } else {
                sb.append(candidate);
            }
            pos = pathEnd;
        }
        return sb.toString();
    }

    private static boolean isPathChar(char c) {
        return Character.isLetterOrDigit(c) || c == '/' || c == '\\' || c == '.'
            || c == '-' || c == '_';
    }

    private boolean isFilePath(String text) {
        if (text == null || text.length() < 5) return false;
        String lower = text.toLowerCase();
        // 파일 확장자 패턴
        boolean hasExt = lower.endsWith(".java") || lower.endsWith(".xml") || lower.endsWith(".jsp")
                      || lower.endsWith(".properties") || lower.endsWith(".yml") || lower.endsWith(".yaml")
                      || lower.endsWith(".sql") || lower.endsWith(".html") || lower.endsWith(".js")
                      || lower.endsWith(".css") || lower.endsWith(".json") || lower.endsWith(".txt");
        // 경로 구분자 포함 여부
        boolean hasPath = text.indexOf('/') >= 0 || text.indexOf('\\') >= 0;
        return hasExt && hasPath;
    }

    /**
     * **path** 형식의 볼드 텍스트에서 파일 경로를 감지하여 `path` 백틱 형식으로 변환
     */
    private String convertBoldFilePaths(String text) {
        StringBuilder sb = new StringBuilder();
        int pos = 0;
        while (pos < text.length()) {
            int boldStart = text.indexOf("**", pos);
            if (boldStart < 0) {
                sb.append(text.substring(pos));
                break;
            }
            sb.append(text.substring(pos, boldStart));
            int boldEnd = text.indexOf("**", boldStart + 2);
            if (boldEnd < 0) {
                sb.append(text.substring(boldStart));
                break;
            }
            String inside = text.substring(boldStart + 2, boldEnd);
            if (isFilePath(inside)) {
                sb.append("`").append(inside).append("`");
            } else {
                sb.append("**").append(inside).append("**");
            }
            pos = boldEnd + 2;
        }
        return sb.toString();
    }

    private String escapeJsString(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("'", "\\'");
    }

    /**
     * 이미 HTML 이스케이프된 코드 텍스트에 구문 강조 span 태그를 적용한다.
     * highlight.js로 클라이언트 사이드에서 처리하므로 여기서는 그대로 반환.
     */
    private String highlightCode(String escaped, String lang) {
        return escaped;
    }

    /**
     * JAR 내부 resources/ 폴더에서 텍스트 파일을 읽어 문자열로 반환.
     * 매번 읽지 않게 캐싱.
     */
    private String hljsCss = null;
    private String hljsJs = null;
    // ── ES5 호환 경량 코드 하이라이터 (IE11/SWT Browser 호환) ──
    private String highlightCssCache;
    private String highlightJsCache;

    private String loadHighlightCss() {
        if (highlightCssCache == null) {
            highlightCssCache =
                "pre code .hl-kw{color:#569cd6}" // keyword
              + "pre code .hl-str{color:#ce9178}" // string
              + "pre code .hl-cm{color:#6a9955}" // comment
              + "pre code .hl-an{color:#dcdcaa}" // annotation
              + "pre code .hl-num{color:#b5cea8}" // number
              + "pre code .hl-cls{color:#4ec9b0}" // class/type
              + "pre code .hl-tag{color:#569cd6}" // xml tag
              + "pre code .hl-attr{color:#9cdcfe}" // xml attribute
              + "pre code .hl-ent{color:#d7ba7d}"; // xml entity
        }
        return highlightCssCache;
    }

    private String loadHighlightJs() {
        if (highlightJsCache == null) {
            highlightJsCache =
                "(function(){"
              // ── Java/JS keywords ──
              + "var JKW='abstract|assert|boolean|break|byte|case|catch|char|class|const|continue|default|do|double|"
              + "else|enum|extends|final|finally|float|for|goto|if|implements|import|instanceof|int|interface|long|"
              + "native|new|package|private|protected|public|return|short|static|strictfp|super|switch|synchronized|"
              + "this|throw|throws|transient|try|void|volatile|while|true|false|null';"
              // ── SQL keywords ──
              + "var SKW='SELECT|FROM|WHERE|INSERT|UPDATE|DELETE|INTO|VALUES|SET|CREATE|ALTER|DROP|TABLE|INDEX|"
              + "JOIN|LEFT|RIGHT|INNER|OUTER|ON|AND|OR|NOT|IN|EXISTS|BETWEEN|LIKE|ORDER|BY|GROUP|HAVING|"
              + "AS|IS|NULL|COUNT|SUM|AVG|MAX|MIN|DISTINCT|UNION|ALL|CASE|WHEN|THEN|ELSE|END|LIMIT|OFFSET|"
              + "VARCHAR|NUMBER|INTEGER|DATE|TIMESTAMP|CONSTRAINT|PRIMARY|KEY|FOREIGN|REFERENCES|COMMIT|"
              + "ROLLBACK|GRANT|REVOKE|SEQUENCE|NEXTVAL|SYSDATE|NVL|DECODE|ROWNUM|SUBSTR|TO_CHAR|TO_DATE';"
              + "var jkwRe=new RegExp('^('+JKW+')$');"
              + "var skwRe=new RegExp('^('+SKW+')$','i');"
              // ── escape helper ──
              + "function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}"
              // ── Java/JS highlighter ──
              + "function hlJava(src){"
              + "var o='',i=0,n=src.length;"
              + "while(i<n){"
              +   "if(src.charAt(i)==='/'&&i+1<n&&src.charAt(i+1)==='/'){" // line comment
              +     "var e=src.indexOf('\\n',i);if(e<0)e=n;"
              +     "o+='<span class=\"hl-cm\">'+esc(src.substring(i,e))+'</span>';i=e;continue;}"
              +   "if(src.charAt(i)==='/'&&i+1<n&&src.charAt(i+1)==='*'){" // block comment
              +     "var e=src.indexOf('*/',i+2);if(e<0)e=n;else e+=2;"
              +     "o+='<span class=\"hl-cm\">'+esc(src.substring(i,e))+'</span>';i=e;continue;}"
              +   "if(src.charAt(i)==='\"'||src.charAt(i)===\"'\"){" // string
              +     "var q=src.charAt(i),k=i+1;"
              +     "while(k<n&&src.charAt(k)!==q){if(src.charAt(k)==='\\\\')k++;k++;}"
              +     "if(k<n)k++;"
              +     "o+='<span class=\"hl-str\">'+esc(src.substring(i,k))+'</span>';i=k;continue;}"
              +   "if(src.charAt(i)==='@'&&i+1<n&&/[A-Za-z]/.test(src.charAt(i+1))){" // annotation
              +     "var k=i+1;while(k<n&&/[A-Za-z0-9_]/.test(src.charAt(k)))k++;"
              +     "o+='<span class=\"hl-an\">'+esc(src.substring(i,k))+'</span>';i=k;continue;}"
              +   "if(/[A-Za-z_$]/.test(src.charAt(i))){" // word
              +     "var k=i;while(k<n&&/[A-Za-z0-9_$]/.test(src.charAt(k)))k++;"
              +     "var w=src.substring(i,k);"
              +     "if(jkwRe.test(w))o+='<span class=\"hl-kw\">'+esc(w)+'</span>';"
              +     "else if(/^[A-Z]/.test(w))o+='<span class=\"hl-cls\">'+esc(w)+'</span>';"
              +     "else o+=esc(w);i=k;continue;}"
              +   "if(/[0-9]/.test(src.charAt(i))){" // number
              +     "var k=i;while(k<n&&/[0-9.xXLlFfDd]/.test(src.charAt(k)))k++;"
              +     "o+='<span class=\"hl-num\">'+esc(src.substring(i,k))+'</span>';i=k;continue;}"
              +   "o+=esc(src.charAt(i));i++;"
              + "}"
              + "return o;}"
              // ── XML/HTML/JSP highlighter ──
              + "function hlXml(src){"
              + "var o='',i=0,n=src.length;"
              + "while(i<n){"
              +   "if(src.charAt(i)==='<'&&i+4<n&&src.substring(i,i+4)==='<!--'){" // comment
              +     "var e=src.indexOf('-->',i+4);if(e<0)e=n;else e+=3;"
              +     "o+='<span class=\"hl-cm\">'+esc(src.substring(i,e))+'</span>';i=e;continue;}"
              +   "if(src.charAt(i)==='<'){" // tag
              +     "var e=src.indexOf('>',i);if(e<0)e=n;else e+=1;"
              +     "var tag=src.substring(i,e);"
              +     "var th=esc(tag);"
              +     "th=th.replace(/([A-Za-z_][A-Za-z0-9_\\-:.]*)=(\\&quot;[^&]*\\&quot;|'[^']*')/g,"
              +       "function(m,a,v){return '<span class=\"hl-attr\">'+a+'</span>=<span class=\"hl-str\">'+v+'</span>';});"
              +     "o+='<span class=\"hl-tag\">'+th+'</span>';i=e;continue;}"
              +   "if(src.charAt(i)==='&'){" // entity
              +     "var e=src.indexOf(';',i);if(e<0||e-i>10){o+=esc(src.charAt(i));i++;continue;}"
              +     "o+='<span class=\"hl-ent\">'+esc(src.substring(i,e+1))+'</span>';i=e+1;continue;}"
              +   "o+=esc(src.charAt(i));i++;"
              + "}"
              + "return o;}"
              // ── SQL highlighter ──
              + "function hlSql(src){"
              + "var o='',i=0,n=src.length;"
              + "while(i<n){"
              +   "if(src.charAt(i)==='-'&&i+1<n&&src.charAt(i+1)==='-'){" // line comment
              +     "var e=src.indexOf('\\n',i);if(e<0)e=n;"
              +     "o+='<span class=\"hl-cm\">'+esc(src.substring(i,e))+'</span>';i=e;continue;}"
              +   "if(src.charAt(i)==='/'&&i+1<n&&src.charAt(i+1)==='*'){" // block comment
              +     "var e=src.indexOf('*/',i+2);if(e<0)e=n;else e+=2;"
              +     "o+='<span class=\"hl-cm\">'+esc(src.substring(i,e))+'</span>';i=e;continue;}"
              +   "if(src.charAt(i)===\"'\"){" // string
              +     "var k=i+1;while(k<n&&src.charAt(k)!==\"'\")k++;if(k<n)k++;"
              +     "o+='<span class=\"hl-str\">'+esc(src.substring(i,k))+'</span>';i=k;continue;}"
              +   "if(/[A-Za-z_]/.test(src.charAt(i))){" // word
              +     "var k=i;while(k<n&&/[A-Za-z0-9_]/.test(src.charAt(k)))k++;"
              +     "var w=src.substring(i,k);"
              +     "if(skwRe.test(w))o+='<span class=\"hl-kw\">'+esc(w)+'</span>';"
              +     "else o+=esc(w);i=k;continue;}"
              +   "if(/[0-9]/.test(src.charAt(i))){var k=i;while(k<n&&/[0-9.]/.test(src.charAt(k)))k++;"
              +     "o+='<span class=\"hl-num\">'+esc(src.substring(i,k))+'</span>';i=k;continue;}"
              +   "o+=esc(src.charAt(i));i++;"
              + "}"
              + "return o;}"
              // ── 전역 노출 (PL 턴제 파일별 하이라이트 재적용용) ──
              + "window.hlJava=hlJava;window.hlXml=hlXml;window.hlSql=hlSql;"
              // ── main: apply highlighting ──
              + "var blocks=document.querySelectorAll('pre code');"
              + "for(var b=0;b<blocks.length;b++){"
              +   "var el=blocks[b];"
              +   "var cls=el.className||'';"
              +   "var src=el.textContent||el.innerText||'';"
              +   "var lang='';"
              +   "var m=cls.match(/language-([a-z]+)/);"
              +   "if(m)lang=m[1];"
              +   "var html;"
              +   "if(lang==='xml'||lang==='html'||lang==='jsp')html=hlXml(src);"
              +   "else if(lang==='sql')html=hlSql(src);"
              +   "else html=hlJava(src);"
              +   "el.innerHTML=html;"
              + "}"
              + "})();";
        }
        return highlightJsCache;
    }

    private String loadHljsCss() {
        if (hljsCss == null) hljsCss = loadResourceAsString("/resources/hljs/vs2015.min.css");
        return hljsCss != null ? hljsCss : "";
    }
    private String loadHljsJs() {
        if (hljsJs == null) hljsJs = loadResourceAsString("/resources/hljs/hljs-bundle.js");
        return hljsJs != null ? hljsJs : "";
    }

    // ── Chat CSS 로딩 ──
    private String chatCssCache;
    private String loadChatCss() {
        if (chatCssCache == null) chatCssCache = loadResourceAsString("/resources/nori-chat.css");
        return chatCssCache != null ? chatCssCache : "";
    }

    // ── PL 워크플로우 CSS/JS 로딩 ──
    private String plCssCache;
    private String plJsCache;
    private String loadPlWorkflowCss() {
        if (plCssCache == null) plCssCache = loadResourceAsString("/resources/pl-workflow.css");
        return plCssCache != null ? plCssCache : "";
    }
    private String loadPlWorkflowJs() {
        if (plJsCache == null) plJsCache = loadResourceAsString("/resources/pl-workflow.js");
        return plJsCache != null ? plJsCache : "";
    }

    private String loadResourceAsString(String path) {
        java.io.InputStream is = null;
        try {
            is = getClass().getResourceAsStream(path);
            if (is == null) {
                // JAR 루트 기준 경로로 재시도
                is = getClass().getClassLoader().getResourceAsStream(path.startsWith("/") ? path.substring(1) : path);
            }
            if (is == null) return null;
            BufferedReader r = new BufferedReader(new InputStreamReader(is, "UTF-8"));
            StringBuilder sb = new StringBuilder();
            char[] buf = new char[8192];
            int n;
            while ((n = r.read(buf)) > 0) sb.append(buf, 0, n);
            r.close();
            return sb.toString();
        } catch (Exception e) {
            return null;
        } finally {
            if (is != null) try { is.close(); } catch (Exception ignored) {}
        }
    }

    /* ── 플레인텍스트 빌드 (Browser 미지원 폴백) ── */

    private String buildPlainText() {
        StringBuilder sb = new StringBuilder();
        if (messages.isEmpty()) {
            sb.append("=== Nori AI ===\n\n");
            sb.append("우클릭 → Nori AI 메뉴로 분석\n");
            sb.append("코드 선택 → 채팅으로 질문\n\n");
            return sb.toString();
        }
        for (int i = 0; i < messages.size(); i++) {
            String[] msg = (String[]) messages.get(i);
            String role = msg[0];
            if ("system".equals(role)) {
                sb.append("--- ").append(msg[1]).append(" ---\n\n");
            } else if ("user".equals(role)) {
                sb.append("[나] ").append(msg[1]).append("\n\n");
            } else {
                String title = msg.length > 2 && msg[2] != null ? msg[2] : "";
                if (title.length() > 0) {
                    sb.append("[볼트 \u2014 ").append(title).append("]\n");
                } else {
                    sb.append("[볼트]\n");
                }
                sb.append(msg[1]).append("\n\n");
            }
        }
        return sb.toString();
    }

    /* ═══════════════════════════════════════════════════════
     *  유틸리티
     * ═══════════════════════════════════════════════════════ */

    private void checkConnection() {
        Job job = new Job("Nori AI - 연결 확인") {
            protected IStatus run(IProgressMonitor monitor) {
                final boolean connected = NoriApiClient.getInstance().checkHealth();
                final String url = NoriApiClient.getInstance().getServerUrl();
                // 프로필 존재 여부 확인 — 워크스페이스 프로젝트 순회 (백그라운드 스레드 안전)
                boolean profileFound = false;
                try {
                    IProject[] projects = ResourcesPlugin.getWorkspace().getRoot().getProjects();
                    for (int pi = 0; pi < projects.length; pi++) {
                        if (projects[pi].isOpen() && projects[pi].getLocation() != null) {
                            File ppf = new File(projects[pi].getLocation().toFile(), PROFILE_FILENAME);
                            if (ppf.exists() && ppf.length() > 100) {
                                profileFound = true;
                                break;
                            }
                        }
                    }
                } catch (Exception e) { /* ignore */ }
                // 프로필 파일이 삭제되었으면 상태 리셋
                if (!profileFound && profileState == 2) {
                    profileState = 0;
                    autoAnalysisTriggered = false;
                }
                if (profileFound && profileState != 1) {
                    profileState = 2;
                }
                // 분석중(1) 상태가 30분 이상 지속되면 고착으로 판단하여 리셋
                if (profileState == 1 && profileAnalysisStartTime > 0) {
                    long elapsed = System.currentTimeMillis() - profileAnalysisStartTime;
                    if (elapsed > 30 * 60 * 1000L) {
                        System.err.println("[Nori] 프로필 분석 30분 초과 — 고착 상태 리셋");
                        profileState = 0;
                        autoAnalysisTriggered = false;
                        profileAnalysisStartTime = 0;
                    }
                }
                Display.getDefault().asyncExec(new Runnable() {
                    public void run() {
                        if (statusLabel == null || statusLabel.isDisposed()) return;
                        statusLabel.setText(connected
                                ? "\u25CF 연결됨 \u2014 " + url
                                : "\u25CB 연결 안됨 \u2014 " + url);
                        // 프로필 존재 시 체크박스 활성화
                        if (profileState == 2 && projectCheck != null && !projectCheck.isDisposed()) {
                            projectCheck.setEnabled(true);
                            projectCheck.setSelection(true);
                            projectCheck.setToolTipText("\ud604\uc7ac \ud504\ub85c\uc81d\ud2b8 \uc18c\uc2a4 \ucf54\ub4dc\ub97c AI\uc5d0 \uc804\ub2ec");
                        }
                        refreshDisplay();
                        // 프로필 없고 서버 연결됨 → 자동 분석 시작
                        if (connected && profileState == 0 && !autoAnalysisTriggered) {
                            autoAnalysisTriggered = true;
                            startAIProjectAnalysis(false);
                        }
                    }
                });
                return Status.OK_STATUS;
            }
        };
        job.setUser(false);
        job.schedule();
    }

    public void setFocus() {
        if (chatInput != null && !chatInput.isDisposed()) {
            chatInput.setFocus();
        }
    }

    public void dispose() {
        if (monoFont != null && !monoFont.isDisposed()) monoFont.dispose();
        if (bgColor != null && !bgColor.isDisposed()) bgColor.dispose();
        if (fgColor != null && !fgColor.isDisposed()) fgColor.dispose();
        super.dispose();
    }

    private static String escapeHtml(String s) {
        if (s == null) return "";
        return s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;");
    }

    private static String nowTimestamp() {
        return new SimpleDateFormat("HH:mm:ss").format(new Date());
    }
}