package nori.ai.plugin;

/**
 * 플러그인 전역 상수
 */
public final class NoriConstants {

    private NoriConstants() {}

    // ── View ID ──
    public static final String VIEW_ID = "nori.ai.views.side";

    // ── Command IDs ──
    public static final String CMD_EXPLAIN       = "nori.ai.command.explain";
    public static final String CMD_REVIEW        = "nori.ai.command.review";
    public static final String CMD_REFACTOR      = "nori.ai.command.refactor";
    public static final String CMD_TEST_GENERATE = "nori.ai.command.testGenerate";
    public static final String CMD_DOC_GENERATE  = "nori.ai.command.docGenerate";
    public static final String CMD_ERROR_ANALYZE = "nori.ai.command.errorAnalyze";
    public static final String CMD_ERROR_FIX     = "nori.ai.command.errorFix";
    public static final String CMD_GENERATE      = "nori.ai.command.generate";
    public static final String CMD_SCHEMA_SCAN   = "nori.ai.command.schemaScan";
    public static final String CMD_API_SCAN      = "nori.ai.command.apiScan";
    public static final String CMD_ERROR_LOG     = "nori.ai.command.errorLog";
    public static final String CMD_CONVENTION    = "nori.ai.command.convention";
    public static final String CMD_PROFILE_UPDATE = "nori.ai.command.profileUpdate";
    public static final String CMD_PROJECT_ANALYSIS_UPDATE = "nori.ai.command.projectAnalysisUpdate";

    // ── Preference Keys ──
    public static final String PREF_SERVER_URL = "nori.ai.serverUrl";
    public static final String PREF_API_KEY    = "nori.ai.apiKey";

    // ── Defaults ──
    public static final String DEFAULT_SERVER_URL = "http://localhost:8090";
}
