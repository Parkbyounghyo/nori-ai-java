package nori.ai.plugin.service;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.Charset;
import java.util.List;

import nori.ai.plugin.NoriConstants;
import nori.ai.plugin.NoriPlugin;

/**
 * Nori AI 서버 HTTP 클라이언트 (Java 8 호환).
 * HttpURLConnection 사용 — 외부 라이브러리 의존성 없음.
 */
public class NoriApiClient {

    private static final Charset UTF8 = Charset.forName("UTF-8");

    private static NoriApiClient instance;

    private NoriApiClient() {}

    public static synchronized NoriApiClient getInstance() {
        if (instance == null) {
            instance = new NoriApiClient();
        }
        return instance;
    }

    // ── SSE 스트리밍 콜백 ──

    /** SSE 스트리밍 이벤트 콜백 인터페이스 */
    public interface StreamCallback {
        /** 서버 상태 메시지 (의도 분류 결과, 태스크 시작 등) */
        void onStatus(String message);
        /** LLM 토큰 수신 */
        void onToken(String content);
        /** 스트리밍 완료 */
        void onDone(String sessionId);
        /** 에러 발생 */
        void onError(String error);
        /** PL 턴제: 파일 1개 처리 완료 — 하이라이트 적용 시점 */
        void onFileDone(String filePath);
        /** PL 턴제: 새 파일 처리 시작 — 진행 표시 */
        void onFileStart(String filePath, int index, int total, int startLine);
    }

    // ── 설정 ──

    public String getServerUrl() {
        NoriPlugin plugin = NoriPlugin.getDefault();
        if (plugin == null) return NoriConstants.DEFAULT_SERVER_URL;
        return plugin.getPreferenceStore().getString(NoriConstants.PREF_SERVER_URL);
    }

    private String getApiKey() {
        NoriPlugin plugin = NoriPlugin.getDefault();
        if (plugin == null) return "";
        return plugin.getPreferenceStore().getString(NoriConstants.PREF_API_KEY);
    }

    // ── 코드 분석 API ──

    public String explain(String code) {
        String body = new JsonBuilder()
                .put("code", code)
                .put("language", "java")
                .build();
        return postAndExtract("/explain", body, "explanation");
    }

    public String explainClass(String code) {
        String body = new JsonBuilder()
                .put("code", code)
                .build();
        return postAndExtract("/explain/class", body, "analysis");
    }

    public String review(String code) {
        String body = new JsonBuilder()
                .put("code", code)
                .put("language", "java")
                .build();
        return postAndExtract("/review", body, "review");
    }

    public String generateDoc(String code) {
        String body = new JsonBuilder()
                .put("code", code)
                .build();
        return postAndExtract("/doc/generate", body, "documented_code");
    }

    // ── 코드 작성 API ──

    public String generateCode(String description) {
        String body = new JsonBuilder()
                .put("description", description)
                .put("language", "java")
                .put("project_type", "spring-boot")
                .build();
        return postAndExtract("/generate", body, "code");
    }

    public String refactor(String code, String instruction) {
        String body = new JsonBuilder()
                .put("code", code)
                .put("instruction", instruction)
                .put("language", "java")
                .build();
        return postAndExtract("/refactor", body, "refactored_code");
    }

    public String generateTest(String code) {
        String body = new JsonBuilder()
                .put("code", code)
                .put("language", "java")
                .put("test_framework", "junit5")
                .build();
        return postAndExtract("/test/generate", body, "test_code");
    }

    // ── 에러/디버그 API ──

    public String analyzeError(String errorMessage, String code) {
        String body = new JsonBuilder()
                .put("error_message", errorMessage)
                .put("code", code)
                .put("java_version", "8")
                .build();
        return postAndExtract("/error/analyze", body, "analysis");
    }

    public String fixError(String errorMessage, String code) {
        String body = new JsonBuilder()
                .put("error_message", errorMessage)
                .put("code", code)
                .build();
        return postAndExtract("/error/fix", body, "fixed_code");
    }

    // ── 프로젝트 스캔 API ──

    public String scanProject(String projectName, String pomXml, String buildGradle,
                              String webXml, String appProps, String fileTree) {
        JsonBuilder jb = new JsonBuilder()
                .put("project_name", projectName);
        if (pomXml != null && pomXml.length() > 0) jb.put("pom_xml", pomXml);
        if (buildGradle != null && buildGradle.length() > 0) jb.put("build_gradle", buildGradle);
        if (webXml != null && webXml.length() > 0) jb.put("web_xml", webXml);
        if (appProps != null && appProps.length() > 0) jb.put("application_properties", appProps);
        if (fileTree != null && fileTree.length() > 0) jb.put("file_tree", fileTree);
        return postAndExtract("/context/scan", jb.build(), "report");
    }

    // ── 채팅 API ──

    private String buildHistoryJson(List history) {
        StringBuilder historyJson = new StringBuilder("[");
        boolean first = true;
        for (int i = 0; i < history.size(); i++) {
            Object o = history.get(i);
            if (!(o instanceof String[])) continue;
            String[] h = (String[]) o;
            if (h.length < 2) continue;
            if (!first) historyJson.append(",");
            historyJson.append("{\"role\":\"")
                    .append(escapeJson(h[0]))
                    .append("\",\"content\":\"")
                    .append(escapeJson(h[1]))
                    .append("\"}");
            first = false;
        }
        historyJson.append("]");
        return historyJson.toString();
    }

    public String chat(String message, List<String[]> history, boolean useRag, String projectContext) {
        String historyJson = buildHistoryJson(history);

        JsonBuilder jb = new JsonBuilder()
                .put("message", message)
                .putRaw("history", historyJson)
                .putBool("use_rag", useRag);
        if (projectContext != null && projectContext.length() > 0) {
            jb.put("project_context", projectContext);
        }
        String body = jb.build();
        return postAndExtract("/chat", body, "answer");
    }

    public boolean deleteSession(String sessionId, String userId) {
        HttpURLConnection conn = null;
        try {
            String sid = URLEncoder.encode(sessionId != null ? sessionId : "", "UTF-8");
            String uid = URLEncoder.encode(userId != null ? userId : "default", "UTF-8");
            URL url = new URL(getServerUrl() + "/api/v1/sessions/" + sid + "?user_id=" + uid);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("DELETE");
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(10000);
            conn.setRequestProperty("Accept", "application/json");

            String apiKey = getApiKey();
            if (apiKey != null && !apiKey.isEmpty()) {
                conn.setRequestProperty("X-API-Key", apiKey);
            }

            int status = conn.getResponseCode();
            return status == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    // ── 프로필 요약 API ──

    public String summarizeProfile(String profileContent) {
        String body = new JsonBuilder()
                .put("profile_content", profileContent)
                .build();
        return postAndExtract("/profile/summarize", body, "summary");
    }

    /** AI로 소스코드 분석하여 1~2줄 비즈니스 설명 생성 */
    public String describeFile(String code, String filename) {
        String body = new JsonBuilder()
                .put("code", code)
                .put("filename", filename)
                .build();
        return postAndExtract("/profile/describe-file", body, "description");
    }

    /**
     * 프로필 + 소스 구조를 서버에 업로드 (프로젝트 갱신 시 호출).
     * @return 성공 시 project_id, 실패 시 null
     */
    public String uploadProfile(String content, String projectId, String name,
                                List sourceFiles) {
        return uploadProfile(content, projectId, name, sourceFiles, "", "", "");
    }

    /**
     * 프로필 + 소스 구조 + 서버 설정 + 워크스페이스 트리 업로드.
     * @param serverXml   server.xml 내용 (없으면 빈 문자열)
     * @param contextXml  context.xml 내용 (없으면 빈 문자열)
     * @param workspaceTree 워크스페이스 전체 프로젝트 구조도
     * @return 성공 시 project_id, 실패 시 null
     */
    public String uploadProfile(String content, String projectId, String name,
                                List sourceFiles,
                                String serverXml, String contextXml,
                                String workspaceTree) {
        StringBuilder sfArr = new StringBuilder("[");
        for (int i = 0; i < sourceFiles.size(); i++) {
            if (i > 0) sfArr.append(",");
            Object item = sourceFiles.get(i);
            String path, cnt;
            if (item instanceof java.util.Map) {
                java.util.Map m = (java.util.Map) item;
                path = String.valueOf(m.get("path"));
                cnt = String.valueOf(m.get("content"));
            } else {
                path = cnt = "";
            }
            sfArr.append("{\"path\":\"").append(escapeJson(path))
                 .append("\",\"content\":\"").append(escapeJson(cnt)).append("\"}");
        }
        sfArr.append("]");

        JsonBuilder jb = new JsonBuilder()
                .put("content", content != null ? content : "")
                .put("project_id", projectId != null ? projectId : "")
                .put("name", name != null ? name : "")
                .putRaw("source_files", sfArr.toString());
        if (serverXml != null && serverXml.length() > 0) {
            jb.put("server_xml", serverXml);
        }
        if (contextXml != null && contextXml.length() > 0) {
            jb.put("context_xml", contextXml);
        }
        if (workspaceTree != null && workspaceTree.length() > 0) {
            jb.put("workspace_tree", workspaceTree);
        }
        return postAndExtract("/profile/upload", jb.build(), "project_id");
    }

    // ── 파일 선별 API ──

    public String pickFiles(String question, String profile) {
        String body = new JsonBuilder()
                .put("question", question)
                .put("profile", profile)
                .build();
        return postAndExtract("/chat/pick-files", body, "files");
    }

    // ── 스마트 채팅 API (1차: 의도분류 + 파일선별) ──

    /** 1차 호출: 의도분류 + 파일선별 → JSON 전체 반환 */
    public String smartChatClassify(String message, List history, boolean useRag,
                                     String profile, String selectedCode) {
        String historyJson = buildHistoryJson(history);

        JsonBuilder jb = new JsonBuilder()
                .put("message", message)
                .put("profile", profile)
            .putRaw("history", historyJson)
                .putBool("use_rag", useRag);
        if (selectedCode != null && selectedCode.length() > 0) {
            jb.put("selected_code", selectedCode);
        }
        return postAndExtract("/chat/smart", jb.build(), null);
    }

    /** 2차 호출: 파일 내용 포함하여 태스크 실행 */
    public String smartChatExecute(String message, List history, boolean useRag,
                                    String profile, String selectedCode,
                                    String fileContentsJson) {
        String historyJson = buildHistoryJson(history);

        JsonBuilder jb = new JsonBuilder()
                .put("message", message)
                .put("profile", profile)
            .putRaw("history", historyJson)
                .putBool("use_rag", useRag)
                .putRaw("file_contents", fileContentsJson);
        if (selectedCode != null && selectedCode.length() > 0) {
            jb.put("selected_code", selectedCode);
        }
        return postAndExtract("/chat/smart", jb.build(), "answer");
    }

    /** SSE 스트리밍 실행 — 태스크를 토큰 단위로 스트리밍 수신 (stopFlag 지원) */
    public void streamSmartExecute(String message, List history, boolean useRag,
                                    String profile, String selectedCode,
                                    String fileContentsJson, String tasksJson,
                                    String sessionId,
                                    StreamCallback callback,
                                    boolean[] stopFlag) {
        HttpURLConnection conn = null;
        try {
            String historyJson = buildHistoryJson(history);

            JsonBuilder jb = new JsonBuilder()
                    .put("message", message)
                    .put("profile", profile)
                .putRaw("history", historyJson)
                    .putBool("use_rag", useRag)
                    .putRaw("file_contents", fileContentsJson)
                    .putRaw("tasks", tasksJson);
            if (selectedCode != null && selectedCode.length() > 0) {
                jb.put("selected_code", selectedCode);
            }
            if (sessionId != null && sessionId.length() > 0) {
                jb.put("session_id", sessionId);
            }
            String body = jb.build();

            URL url = new URL(getServerUrl() + "/api/v1/chat/smart/stream");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(600000);
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            conn.setRequestProperty("Accept", "text/event-stream");

            String apiKey = getApiKey();
            if (apiKey != null && !apiKey.isEmpty()) {
                conn.setRequestProperty("X-API-Key", apiKey);
            }

            byte[] bodyBytes = body.getBytes(UTF8);
            conn.setFixedLengthStreamingMode(bodyBytes.length);
            OutputStream os = conn.getOutputStream();
            os.write(bodyBytes);
            os.flush();
            os.close();

            int status = conn.getResponseCode();
            if (status != 200) {
                // 에러 응답 본문에서 메시지 추출 시도
                String errBody = "";
                try {
                    java.io.InputStream es = conn.getErrorStream();
                    if (es != null) {
                        BufferedReader er = new BufferedReader(new InputStreamReader(es, UTF8));
                        StringBuilder sb = new StringBuilder();
                        String el;
                        while ((el = er.readLine()) != null && sb.length() < 500) {
                            sb.append(el);
                        }
                        er.close();
                        errBody = sb.toString();
                    }
                } catch (Exception ignored) { }
                String detail = errBody.length() > 0
                        ? "\uc11c\ubc84 \uc5d0\ub7ec (HTTP " + status + "): " + errBody.substring(0, Math.min(errBody.length(), 200))
                        : "\uc11c\ubc84 \uc5d0\ub7ec (HTTP " + status + ")";
                callback.onError(detail);
                return;
            }

            // SSE 스트림 파싱
            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(conn.getInputStream(), UTF8));
            String eventType = null;
            StringBuilder dataBuffer = new StringBuilder();
            String line;
            boolean doneReceived = false;
            boolean anyTokenReceived = false;

            while ((line = reader.readLine()) != null) {
                if (stopFlag != null && stopFlag[0]) {
                    callback.onDone(null);
                    doneReceived = true;
                    break;
                }
                if (line.startsWith("event: ")) {
                    eventType = line.substring(7).trim();
                } else if (line.startsWith("data: ")) {
                    dataBuffer.append(line.substring(6));
                } else if (line.isEmpty() && eventType != null) {
                    String data = dataBuffer.toString();

                    if ("status".equals(eventType)) {
                        String msg = extractJsonField(data, "message");
                        String step = extractJsonField(data, "step");
                        if ("file_read".equals(step) || "chunk_read".equals(step)) {
                            // 청크 읽기 진행: onStatus에 [CHUNK] 접두어로 전달
                            if (msg != null) callback.onStatus("[CHUNK]" + msg);
                        } else if ("file_start".equals(step)) {
                            // PL 턴제: 새 파일 처리 시작
                            String fp = extractJsonField(data, "file");
                            int idx = parseJsonInt(data, "index", 0);
                            int total = parseJsonInt(data, "total", 1);
                            int startLine = parseJsonInt(data, "line", 1);
                            callback.onFileStart(fp != null ? fp : "", idx, total, startLine);
                        } else if (msg != null) {
                            callback.onStatus(msg);
                        }
                        // PL 턴제: file_done step이면 하이라이트 트리거
                        if ("file_done".equals(step)) {
                            String fp = extractJsonField(data, "file");
                            callback.onFileDone(fp != null ? fp : "");
                        }
                    } else if ("token".equals(eventType)) {
                        String content = extractJsonField(data, "content");
                        if (content != null) { callback.onToken(content); anyTokenReceived = true; }
                    } else if ("done".equals(eventType)) {
                        String sid = extractJsonField(data, "session_id");
                        callback.onDone(sid);
                        doneReceived = true;
                    }

                    eventType = null;
                    dataBuffer.setLength(0);
                }
            }
            reader.close();

            // 스트림 종료 후 미처리 이벤트 처리
            // (서버가 done 이벤트 직후 연결을 닫아 빈 줄 구분자가 도착하지 않은 경우)
            if (!doneReceived && eventType != null) {
                String data = dataBuffer.toString();
                if ("done".equals(eventType)) {
                    String sid = extractJsonField(data, "session_id");
                    callback.onDone(sid);
                    doneReceived = true;
                } else if ("token".equals(eventType)) {
                    String content = extractJsonField(data, "content");
                    if (content != null) callback.onToken(content);
                }
            }

            // 서버가 done 이벤트 없이 연결 종료된 경우
            if (!doneReceived) {
                // 토큰을 일부라도 받았으면 정상 종료로 간주
                if (anyTokenReceived) {
                    callback.onDone(null);
                } else {
                    callback.onError("\uc11c\ubc84 \uc5f0\uacb0\uc774 \ub04a\uc5b4\uc84c\uc2b5\ub2c8\ub2e4. (done \ubbf8\uc218\uc2e0, lastEvent=" + (eventType != null ? eventType : "null") + ", data=" + dataBuffer.length() + "B) \ub2e4\uc2dc \uc2dc\ub3c4\ud574\uc8fc\uc138\uc694.");
                }
            }

        } catch (java.net.ConnectException e) {
            callback.onError("\uc11c\ubc84\uc5d0 \uc5f0\uacb0\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.");
        } catch (java.net.SocketTimeoutException e) {
            callback.onError("\uc694\uccad \uc2dc\uac04\uc774 \ucd08\uacfc\ub418\uc5c8\uc2b5\ub2c8\ub2e4. (10\ubd84)");
        } catch (java.io.IOException e) {
            callback.onError("IO\uc624\ub958: " + e.getClass().getSimpleName() + ": " + e.getMessage());
        } catch (Exception e) {
            callback.onError("\uc624\ub958: " + e.getClass().getSimpleName() + ": " + e.getMessage());
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    // ── 메모 API (DB 스키마, API 매핑, 에러 로그, 코딩 컨벤션) ──

    public String scanSchema(List sourceFiles) {
        StringBuilder arr = new StringBuilder("[");
        for (int i = 0; i < sourceFiles.size(); i++) {
            if (i > 0) arr.append(",");
            arr.append("\"").append(escapeJson((String) sourceFiles.get(i))).append("\"");
        }
        arr.append("]");
        String body = new JsonBuilder()
                .putRaw("source_files", arr.toString())
                .put("project_type", "egov")
                .build();
        return postAndExtract("/memo/scan-schema", body, "message");
    }

    public String scanApi(List controllerFiles) {
        StringBuilder arr = new StringBuilder("[");
        for (int i = 0; i < controllerFiles.size(); i++) {
            if (i > 0) arr.append(",");
            arr.append("\"").append(escapeJson((String) controllerFiles.get(i))).append("\"");
        }
        arr.append("]");
        String body = new JsonBuilder()
                .putRaw("controller_files", arr.toString())
                .put("project_type", "egov")
                .build();
        return postAndExtract("/memo/scan-api", body, "message");
    }

    public String addErrorLog(String symptom, String solution, String category) {
        String body = new JsonBuilder()
                .put("symptom", symptom)
                .put("solution", solution)
                .put("category", category)
                .build();
        return postAndExtract("/memo/error-log", body, "message");
    }

    public String saveConvention(List rules) {
        StringBuilder arr = new StringBuilder("[");
        for (int i = 0; i < rules.size(); i++) {
            if (i > 0) arr.append(",");
            arr.append("\"").append(escapeJson((String) rules.get(i))).append("\"");
        }
        arr.append("]");
        String body = new JsonBuilder()
                .putRaw("rules", arr.toString())
                .build();
        return postAndExtract("/memo/convention", body, "message");
    }

    public String getConvention() {
        return getAndExtractRaw("/memo/convention");
    }

    public String getErrorLogs() {
        return getAndExtractRaw("/memo/error-log");
    }

    public String getSchema() {
        return getAndExtractRaw("/memo/schema");
    }

    public String getApiMapping() {
        return getAndExtractRaw("/memo/api-mapping");
    }

    // ── PL 워크플로우 API ──

    /** PL 분석: 사용자 요청을 분석하여 대상 파일 + 수정 순서 반환 (raw JSON) */
    public String plAnalyze(String request, String projectProfile, String fileTree, String dependencyMap) {
        JsonBuilder jb = new JsonBuilder()
                .put("user_request", request);
        if (projectProfile != null && projectProfile.length() > 0) {
            jb.put("project_profile", projectProfile);
        }
        if (fileTree != null && fileTree.length() > 0) {
            jb.put("file_tree", fileTree);
        }
        if (dependencyMap != null && dependencyMap.length() > 2) {
            jb.putRaw("dependency_map", dependencyMap);
        }
        return postAndExtract("/pl/analyze", jb.build(), null);
    }

    /** PL 분석 + TODO 자동 생성 (raw JSON) */
    public String plAnalyzeAndCreate(String request, String projectProfile, String fileTree, String dependencyMap) {
        JsonBuilder jb = new JsonBuilder()
                .put("user_request", request);
        if (projectProfile != null && projectProfile.length() > 0) {
            jb.put("project_profile", projectProfile);
        }
        if (fileTree != null && fileTree.length() > 0) {
            jb.put("file_tree", fileTree);
        }
        if (dependencyMap != null && dependencyMap.length() > 2) {
            jb.putRaw("dependency_map", dependencyMap);
        }
        return postAndExtract("/pl/analyze-and-create", jb.build(), null);
    }

    /** TODO 목록 조회 (raw JSON) */
    public String plGetTodos() {
        return getAndExtractRaw("/pl/todos");
    }

    /** TODO 단건 조회 (raw JSON) */
    public String plGetTodo(String todoId) {
        return getAndExtractRaw("/pl/todo/" + todoId);
    }

    /** TODO 항목 상태 업데이트 (raw JSON) */
    public String plUpdateTodoItem(String todoId, int order, String status) {
        String body = new JsonBuilder()
                .put("todo_id", todoId)
                .putInt("order", order)
                .put("status", status)
                .build();
        return postAndExtract("/pl/todo/item", body, null);
    }

    /** 소스 제안 요청 (raw JSON — 파일 1건에 대한 소스코드 생성) */
    public String plSuggestSource(String todoId, int order, String fileContent) {
        JsonBuilder jb = new JsonBuilder()
                .put("todo_id", todoId)
                .putInt("order", order);
        if (fileContent != null && fileContent.length() > 0) {
            jb.put("file_content", fileContent);
        }
        return postAndExtract("/pl/suggest", jb.build(), null);
    }

    /** 소스 다시 생성 (raw JSON) */
    public String plRetrySource(String todoId, int order, String feedback) {
        JsonBuilder jb = new JsonBuilder()
                .put("todo_id", todoId)
                .putInt("order", order);
        if (feedback != null && feedback.length() > 0) {
            jb.put("feedback", feedback);
        }
        return postAndExtract("/pl/retry", jb.build(), null);
    }

    /** 피드백 저장 */
    public String plSaveFeedback(String todoId, int order, String fileName,
                                  String type, String reason) {
        JsonBuilder jb = new JsonBuilder()
                .put("todo_id", todoId)
                .putInt("order", order)
                .put("file_name", fileName)
                .put("type", type);
        if (reason != null && reason.length() > 0) {
            jb.put("reason", reason);
        }
        return postAndExtract("/pl/feedback", jb.build(), null);
    }

    /** 보고서 생성 (raw JSON) */
    public String plGenerateReport(String todoId) {
        return postAndExtract("/pl/report/" + todoId, "{}", null);
    }

    /** 히스토리 조회 (raw JSON) */
    public String plGetHistory() {
        return getAndExtractRaw("/pl/history");
    }

    // ── 수집/임베딩 현황 API ──

    /** 수집/임베딩 현황 조회 (raw JSON) */
    public String getCollectorStatus() {
        return getAndExtractRaw("/admin/status");
    }

    // ── 헬스체크 ──

    public boolean checkHealth() {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getServerUrl() + "/api/v1/health");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);
            int status = conn.getResponseCode();
            return status == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    // ── 내부 메서드 ──

    private String getAndExtractRaw(String endpoint) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getServerUrl() + "/api/v1" + endpoint);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(30000);
            conn.setRequestProperty("Accept", "application/json");

            String apiKey = getApiKey();
            if (apiKey != null && !apiKey.isEmpty()) {
                conn.setRequestProperty("X-API-Key", apiKey);
            }

            int status = conn.getResponseCode();
            BufferedReader reader;
            if (status >= 200 && status < 300) {
                reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), UTF8));
            } else {
                reader = new BufferedReader(new InputStreamReader(conn.getErrorStream(), UTF8));
            }

            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                sb.append(line);
            }
            reader.close();

            if (status != 200) {
                return "\uc11c\ubc84 \uc5d0\ub7ec (HTTP " + status + ")";
            }
            return sb.toString();

        } catch (java.net.ConnectException e) {
            return "\uc11c\ubc84\uc5d0 \uc5f0\uacb0\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.";
        } catch (Exception e) {
            return "\uc624\ub958: " + e.getMessage();
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private String postAndExtract(String endpoint, String body, String field) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getServerUrl() + "/api/v1" + endpoint);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(600000); // 10분 — 로컬 LLM 응답 대기
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
            conn.setRequestProperty("Accept", "application/json");

            String apiKey = getApiKey();
            if (apiKey != null && !apiKey.isEmpty()) {
                conn.setRequestProperty("X-API-Key", apiKey);
            }

            byte[] bodyBytes = body.getBytes(UTF8);
            conn.setFixedLengthStreamingMode(bodyBytes.length);
            OutputStream os = conn.getOutputStream();
            os.write(bodyBytes);
            os.flush();
            os.close();

            int status = conn.getResponseCode();
            BufferedReader reader;
            if (status >= 200 && status < 300) {
                reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), UTF8));
            } else {
                reader = new BufferedReader(new InputStreamReader(conn.getErrorStream(), UTF8));
            }

            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                sb.append(line);
            }
            reader.close();
            String json = sb.toString();

            if (status != 200) {
                String error = extractJsonField(json, "error");
                return error != null
                        ? "\uc5d0\ub7ec: " + error
                        : "\uc11c\ubc84 \uc5d0\ub7ec (HTTP " + status + ")";
            }

            // field가 null이면 raw JSON 전체 반환
            if (field == null) return json;

            String result = extractJsonField(json, field);
            return result != null ? result : "\uc751\ub2f5\uc744 \ud30c\uc2f1\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.";

        } catch (java.net.ConnectException e) {
            return "\uc11c\ubc84\uc5d0 \uc5f0\uacb0\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.\nNori AI \uc11c\ubc84\uac00 \uc2e4\ud589 \uc911\uc778\uc9c0 \ud655\uc778\ud558\uc138\uc694.\n(" + getServerUrl() + ")";
        } catch (java.net.SocketTimeoutException e) {
            return "\uc694\uccad \uc2dc\uac04\uc774 \ucd08\uacfc\ub418\uc5c8\uc2b5\ub2c8\ub2e4. \uc11c\ubc84 \uc0c1\ud0dc\ub97c \ud655\uc778\ud558\uc138\uc694.";
        } catch (Exception e) {
            return "\uc624\ub958: " + e.getMessage();
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    // ── JSON 유틸리티 ──

    /**
     * JSON 문자열에서 지정한 필드의 문자열 값을 추출한다.
     * 외부 JSON 라이브러리 없이 수동 문자 탐색으로 처리.
     * (정규식은 긴 문자열에서 StackOverflowError 발생 가능)
     */
    public static String extractJsonField(String json, String fieldName) {
        if (json == null) return null;

        String key = "\"" + fieldName + "\"";
        int keyIdx = json.indexOf(key);
        while (keyIdx >= 0) {
            // 키 뒤의 ':' 찾기
            int colonIdx = keyIdx + key.length();
            while (colonIdx < json.length() && json.charAt(colonIdx) == ' ') colonIdx++;
            if (colonIdx >= json.length() || json.charAt(colonIdx) != ':') {
                keyIdx = json.indexOf(key, keyIdx + 1);
                continue;
            }

            // ':' 뒤 공백 건너뛰기
            int valStart = colonIdx + 1;
            while (valStart < json.length() && json.charAt(valStart) == ' ') valStart++;
            if (valStart >= json.length()) return null;

            // null 체크
            if (json.length() >= valStart + 4
                    && json.substring(valStart, valStart + 4).equals("null")) {
                return null;
            }

            // 문자열 값이 아니면 다음 키 검색
            if (json.charAt(valStart) != '"') {
                keyIdx = json.indexOf(key, keyIdx + 1);
                continue;
            }

            // 문자열 값 수동 추출 (이스케이프 처리)
            StringBuilder sb = new StringBuilder();
            int i = valStart + 1;
            while (i < json.length()) {
                char c = json.charAt(i);
                if (c == '\\' && i + 1 < json.length()) {
                    char next = json.charAt(i + 1);
                    switch (next) {
                        case 'n': sb.append('\n'); break;
                        case 't': sb.append('\t'); break;
                        case 'r': sb.append('\r'); break;
                        case '"': sb.append('"'); break;
                        case '/': sb.append('/'); break;
                        case '\\': sb.append('\\'); break;
                        default: sb.append(next); break;
                    }
                    i += 2;
                } else if (c == '"') {
                    return sb.toString();
                } else {
                    sb.append(c);
                    i++;
                }
            }
            return null;
        }
        return null;
    }

    public static String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    /** JSON에서 정수 필드 추출 (예: "index":3) */
    static int parseJsonInt(String json, String fieldName, int defaultValue) {
        if (json == null) return defaultValue;
        String key = "\"" + fieldName + "\"";
        int keyIdx = json.indexOf(key);
        if (keyIdx < 0) return defaultValue;
        int colonIdx = keyIdx + key.length();
        while (colonIdx < json.length() && json.charAt(colonIdx) == ' ') colonIdx++;
        if (colonIdx >= json.length() || json.charAt(colonIdx) != ':') return defaultValue;
        int valStart = colonIdx + 1;
        while (valStart < json.length() && json.charAt(valStart) == ' ') valStart++;
        StringBuilder sb = new StringBuilder();
        for (int i = valStart; i < json.length(); i++) {
            char c = json.charAt(i);
            if (c >= '0' && c <= '9') sb.append(c);
            else break;
        }
        if (sb.length() == 0) return defaultValue;
        try { return Integer.parseInt(sb.toString()); }
        catch (NumberFormatException e) { return defaultValue; }
    }

    // ── JSON Builder (내장, 외부 의존성 없음) ──

    static class JsonBuilder {
        private final StringBuilder sb = new StringBuilder("{");
        private boolean first = true;

        JsonBuilder put(String key, String value) {
            addComma();
            sb.append("\"").append(key).append("\":\"")
              .append(escapeJson(value)).append("\"");
            return this;
        }

        JsonBuilder putBool(String key, boolean value) {
            addComma();
            sb.append("\"").append(key).append("\":").append(value);
            return this;
        }

        JsonBuilder putInt(String key, int value) {
            addComma();
            sb.append("\"").append(key).append("\":").append(value);
            return this;
        }

        JsonBuilder putRaw(String key, String rawJson) {
            addComma();
            sb.append("\"").append(key).append("\":").append(rawJson);
            return this;
        }

        String build() {
            return sb.append("}").toString();
        }

        private void addComma() {
            if (!first) sb.append(",");
            first = false;
        }
    }
}
