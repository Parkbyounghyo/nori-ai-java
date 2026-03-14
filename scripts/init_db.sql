-- ============================================================
-- Nori AI Java Project - Structure Index Database
-- 컨셉변경.md 기준 DB 생성 스크립트
-- ============================================================

CREATE DATABASE IF NOT EXISTS nori_java
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE nori_java;

-- ============================================================
-- 10.1 project_files - 파일 정보
-- ============================================================
CREATE TABLE IF NOT EXISTS project_files (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_path VARCHAR(500) NOT NULL,
    file_name VARCHAR(255),
    file_type VARCHAR(50),
    package_name VARCHAR(255),
    last_modified DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.2 project_classes - 클래스 정보
-- ============================================================
CREATE TABLE IF NOT EXISTS project_classes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_id BIGINT NOT NULL,
    class_name VARCHAR(200) NOT NULL,
    package_name VARCHAR(255),
    class_type VARCHAR(50),
    full_name VARCHAR(400),
    summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_classes_file FOREIGN KEY (file_id) REFERENCES project_files(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.3 project_methods - 메서드 정보
-- ============================================================
CREATE TABLE IF NOT EXISTS project_methods (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    class_id BIGINT NOT NULL,
    method_name VARCHAR(200) NOT NULL,
    full_name VARCHAR(400),
    return_type VARCHAR(100),
    parameters TEXT,
    access_modifier VARCHAR(30),
    is_static CHAR(1) DEFAULT 'N',
    start_line INT,
    end_line INT,
    summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_methods_class FOREIGN KEY (class_id) REFERENCES project_classes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.4 project_calls - 함수 호출 관계
-- ============================================================
CREATE TABLE IF NOT EXISTS project_calls (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    caller_method_id BIGINT NOT NULL,
    callee_method_id BIGINT NOT NULL,
    call_type VARCHAR(50) DEFAULT 'CALL',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_calls_caller FOREIGN KEY (caller_method_id) REFERENCES project_methods(id) ON DELETE CASCADE,
    CONSTRAINT fk_calls_callee FOREIGN KEY (callee_method_id) REFERENCES project_methods(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.5 project_pages - 페이지 정보 (JSP / HTML)
-- ============================================================
CREATE TABLE IF NOT EXISTS project_pages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    page_name VARCHAR(200),
    file_id BIGINT,
    file_path VARCHAR(500),
    url VARCHAR(300),
    summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_pages_file FOREIGN KEY (file_id) REFERENCES project_files(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.6 project_routes - URL 매핑
-- ============================================================
CREATE TABLE IF NOT EXISTS project_routes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    url VARCHAR(300) NOT NULL,
    http_method VARCHAR(20),
    controller_class_id BIGINT,
    controller_method_id BIGINT,
    summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_routes_class FOREIGN KEY (controller_class_id) REFERENCES project_classes(id) ON DELETE SET NULL,
    CONSTRAINT fk_routes_method FOREIGN KEY (controller_method_id) REFERENCES project_methods(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.7 project_tables - DB 테이블 정보
-- ============================================================
CREATE TABLE IF NOT EXISTS project_tables (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    table_name VARCHAR(200) NOT NULL,
    schema_name VARCHAR(100),
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.8 project_sqls - SQL / Mapper XML 정보
-- ============================================================
CREATE TABLE IF NOT EXISTS project_sqls (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_id BIGINT,
    mapper_name VARCHAR(200),
    sql_id VARCHAR(200),
    sql_type VARCHAR(30),
    related_table_names TEXT,
    summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_sqls_file FOREIGN KEY (file_id) REFERENCES project_files(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.9 project_features - 기능 단위 정의
-- ============================================================
CREATE TABLE IF NOT EXISTS project_features (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    feature_name VARCHAR(200) NOT NULL,
    description TEXT,
    entry_point_type VARCHAR(50),
    entry_point_id BIGINT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.10 project_feature_nodes - 기능과 코드 연결
-- ============================================================
CREATE TABLE IF NOT EXISTS project_feature_nodes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    feature_id BIGINT NOT NULL,
    node_type VARCHAR(50) NOT NULL,
    node_id BIGINT NOT NULL,
    sort_order INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_feature_nodes_feature FOREIGN KEY (feature_id) REFERENCES project_features(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.11 project_graph_nodes - Call Graph 노드 저장
-- ============================================================
CREATE TABLE IF NOT EXISTS project_graph_nodes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    node_type VARCHAR(50) NOT NULL,
    node_key VARCHAR(500) NOT NULL,
    node_name VARCHAR(300) NOT NULL,
    ref_table VARCHAR(100),
    ref_id BIGINT,
    file_id BIGINT,
    summary TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_graph_nodes_file FOREIGN KEY (file_id) REFERENCES project_files(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.12 project_graph_edges - Call Graph 간선 저장
-- ============================================================
CREATE TABLE IF NOT EXISTS project_graph_edges (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    from_node_id BIGINT NOT NULL,
    to_node_id BIGINT NOT NULL,
    edge_type VARCHAR(50) NOT NULL,
    edge_label VARCHAR(100),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_graph_edges_from FOREIGN KEY (from_node_id) REFERENCES project_graph_nodes(id) ON DELETE CASCADE,
    CONSTRAINT fk_graph_edges_to FOREIGN KEY (to_node_id) REFERENCES project_graph_nodes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.13 project_page_scripts - 페이지와 JS 연결
-- ============================================================
CREATE TABLE IF NOT EXISTS project_page_scripts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    page_id BIGINT NOT NULL,
    script_file_id BIGINT NOT NULL,
    relation_type VARCHAR(50) DEFAULT 'LINKS_TO',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_page_scripts_page FOREIGN KEY (page_id) REFERENCES project_pages(id) ON DELETE CASCADE,
    CONSTRAINT fk_page_scripts_script FOREIGN KEY (script_file_id) REFERENCES project_files(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.14 project_script_apis - JS와 API 연결
-- ============================================================
CREATE TABLE IF NOT EXISTS project_script_apis (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    script_file_id BIGINT NOT NULL,
    api_url VARCHAR(300) NOT NULL,
    http_method VARCHAR(20),
    function_name VARCHAR(200),
    relation_type VARCHAR(50) DEFAULT 'TRIGGERS',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_script_apis_script FOREIGN KEY (script_file_id) REFERENCES project_files(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10.15 project_sql_tables - SQL과 테이블 연결
-- ============================================================
CREATE TABLE IF NOT EXISTS project_sql_tables (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    sql_id BIGINT NOT NULL,
    table_id BIGINT NOT NULL,
    relation_type VARCHAR(30) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_sql_tables_sql FOREIGN KEY (sql_id) REFERENCES project_sqls(id) ON DELETE CASCADE,
    CONSTRAINT fk_sql_tables_table FOREIGN KEY (table_id) REFERENCES project_tables(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 11. 인덱스 생성
-- ============================================================
CREATE INDEX idx_project_files_path ON project_files(file_path(255));
CREATE INDEX idx_project_classes_name ON project_classes(class_name);
CREATE INDEX idx_project_methods_name ON project_methods(method_name);
CREATE INDEX idx_project_methods_full_name ON project_methods(full_name(255));
CREATE INDEX idx_project_routes_url ON project_routes(url(255));
CREATE INDEX idx_project_tables_name ON project_tables(table_name);
CREATE INDEX idx_project_sqls_sql_id ON project_sqls(sql_id);
CREATE INDEX idx_project_graph_nodes_type_key ON project_graph_nodes(node_type, node_key(200));
CREATE INDEX idx_project_graph_edges_from_to ON project_graph_edges(from_node_id, to_node_id);
CREATE INDEX idx_project_graph_edges_type ON project_graph_edges(edge_type);

-- ============================================================
-- 검증
-- ============================================================
SHOW TABLES;
