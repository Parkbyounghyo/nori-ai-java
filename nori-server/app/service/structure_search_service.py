"""
Structure Search Service — MariaDB(nori_java) 기반 구조 검색

intent 별 검색 전략:
  feature_location  → 기능/클래스/메서드 위치 검색
  flow_trace        → 호출 흐름 추적 (forward)
  data_flow         → URL → Controller → Mapper → SQL → Table
  page_connection   → JSP → JS → API 연결
  impact_analysis   → 변경 영향도 분석 (역방향 호출)
  reverse_trace     → Table 기준 역추적
  struct_query      → 전체 구조 요약
"""
import logging
from typing import Any

from app.db.connection import get_conn

logger = logging.getLogger("nori-server")


class StructureSearchService:

    # ────────────────────────────────────────────────
    # 기능 위치 검색
    # ────────────────────────────────────────────────
    async def search_feature_location(self, keyword: str) -> dict[str, Any]:
        """키워드로 클래스 / 메서드 / 기능 위치 검색"""
        like = f"%{keyword}%"
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                # 클래스 검색
                await cur.execute(
                    """
                    SELECT c.id, c.class_name, c.class_type, c.full_name, c.summary,
                           f.file_path
                    FROM project_classes c
                    JOIN project_files f ON c.file_id = f.id
                    WHERE c.class_name LIKE %s OR c.full_name LIKE %s OR c.summary LIKE %s
                    LIMIT 10
                    """,
                    (like, like, like),
                )
                classes = await cur.fetchall()

                # 메서드 검색
                await cur.execute(
                    """
                    SELECT m.id, m.method_name, m.full_name, m.summary, m.parameters,
                           c.class_name, c.class_type, f.file_path
                    FROM project_methods m
                    JOIN project_classes c ON m.class_id = c.id
                    JOIN project_files f ON c.file_id = f.id
                    WHERE m.method_name LIKE %s OR m.full_name LIKE %s OR m.summary LIKE %s
                    LIMIT 10
                    """,
                    (like, like, like),
                )
                methods = await cur.fetchall()

                # 기능 인덱스 검색
                await cur.execute(
                    """
                    SELECT id, feature_name, description, entry_point_type
                    FROM project_features
                    WHERE feature_name LIKE %s OR description LIKE %s
                    LIMIT 5
                    """,
                    (like, like),
                )
                features = await cur.fetchall()

        return {
            "keyword": keyword,
            "classes": [_row_to_dict(r, ["id", "class_name", "class_type", "full_name", "summary", "file_path"]) for r in classes],
            "methods": [_row_to_dict(r, ["id", "method_name", "full_name", "summary", "parameters", "class_name", "class_type", "file_path"]) for r in methods],
            "features": [_row_to_dict(r, ["id", "feature_name", "description", "entry_point_type"]) for r in features],
        }

    # ────────────────────────────────────────────────
    # 호출 흐름 추적 (정방향)
    # ────────────────────────────────────────────────
    async def trace_flow(self, method_full_name: str, depth: int = 5) -> dict[str, Any]:
        """메서드 full_name 기준 호출 흐름 정방향 추적 (BFS, depth 제한)"""
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                # 시작 메서드 찾기
                await cur.execute(
                    "SELECT id, method_name, full_name FROM project_methods WHERE full_name = %s LIMIT 1",
                    (method_full_name,),
                )
                start = await cur.fetchone()
                if not start:
                    return {"error": f"메서드를 찾을 수 없습니다: {method_full_name}"}

                flow = await _bfs_call_graph(cur, start[0], direction="forward", max_depth=depth)

        return {
            "start": {"id": start[0], "method_name": start[1], "full_name": start[2]},
            "flow": flow,
        }

    # ────────────────────────────────────────────────
    # 데이터 흐름 (URL → Table)
    # ────────────────────────────────────────────────
    async def trace_data_flow(self, url: str) -> dict[str, Any]:
        """URL 기준 데이터 흐름 추적 (Route → Controller → Mapper → SQL → Table)"""
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                # URL로 route 찾기
                await cur.execute(
                    """
                    SELECT r.id, r.url, r.http_method,
                           c.class_name, c.full_name AS ctrl_full,
                           m.method_name, m.full_name AS method_full
                    FROM project_routes r
                    LEFT JOIN project_classes c ON r.controller_class_id = c.id
                    LEFT JOIN project_methods m ON r.controller_method_id = m.id
                    WHERE r.url = %s OR r.url LIKE %s
                    LIMIT 1
                    """,
                    (url, f"%{url}%"),
                )
                route = await cur.fetchone()

                if not route:
                    return {"error": f"URL을 찾을 수 없습니다: {url}"}

                route_dict = _row_to_dict(route, ["id", "url", "http_method", "class_name", "ctrl_full", "method_name", "method_full"])

                # Controller → 하위 호출 흐름 (graph_nodes 기준)
                await cur.execute(
                    """
                    SELECT gn.node_type, gn.node_name, gn.node_key, gn.summary
                    FROM project_graph_nodes gn
                    WHERE gn.node_key = %s
                    LIMIT 1
                    """,
                    (url,),
                )
                api_node = await cur.fetchone()

                flow_nodes = []
                if api_node:
                    flow_nodes = await _traverse_graph(cur, api_node[2], direction="forward", max_depth=8)

                # table 추출
                await cur.execute(
                    """
                    SELECT pt.table_name, pt.description, pst.relation_type
                    FROM project_sql_tables pst
                    JOIN project_tables pt ON pst.table_id = pt.id
                    JOIN project_sqls ps ON pst.sql_id = ps.id
                    WHERE ps.file_id IN (
                        SELECT file_id FROM project_classes
                        WHERE full_name = %s
                    )
                    LIMIT 20
                    """,
                    (route_dict.get("ctrl_full", ""),),
                )
                tables = await cur.fetchall()

        return {
            "url": url,
            "route": route_dict,
            "flow": flow_nodes,
            "tables": [_row_to_dict(t, ["table_name", "description", "relation_type"]) for t in tables],
        }

    # ────────────────────────────────────────────────
    # 화면 연결 (JSP → JS → API)
    # ────────────────────────────────────────────────
    async def trace_page_connection(self, keyword: str) -> dict[str, Any]:
        """페이지 키워드 기준 JSP → JS → API 연결 추적"""
        like = f"%{keyword}%"
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                # 페이지 찾기
                await cur.execute(
                    """
                    SELECT p.id, p.page_name, p.file_path, p.url, p.summary
                    FROM project_pages p
                    WHERE p.page_name LIKE %s OR p.file_path LIKE %s OR p.url LIKE %s
                    LIMIT 5
                    """,
                    (like, like, like),
                )
                pages = await cur.fetchall()

                result_pages = []
                for page in pages:
                    page_id = page[0]
                    p_dict = _row_to_dict(page, ["id", "page_name", "file_path", "url", "summary"])

                    # JSP → JS
                    await cur.execute(
                        """
                        SELECT f.file_path, f.file_name, ps.relation_type
                        FROM project_page_scripts ps
                        JOIN project_files f ON ps.script_file_id = f.id
                        WHERE ps.page_id = %s
                        """,
                        (page_id,),
                    )
                    scripts = await cur.fetchall()
                    p_dict["scripts"] = [_row_to_dict(s, ["file_path", "file_name", "relation_type"]) for s in scripts]

                    # JS → API
                    api_list = []
                    for s in scripts:
                        script_file_id = None
                        # file_path로 file_id 조회
                        await cur.execute(
                            "SELECT id FROM project_files WHERE file_path = %s LIMIT 1",
                            (s[0],),
                        )
                        frow = await cur.fetchone()
                        if frow:
                            script_file_id = frow[0]
                            await cur.execute(
                                """
                                SELECT sa.api_url, sa.http_method, sa.function_name, sa.relation_type
                                FROM project_script_apis sa
                                WHERE sa.script_file_id = %s
                                """,
                                (script_file_id,),
                            )
                            apis = await cur.fetchall()
                            api_list.extend([_row_to_dict(a, ["api_url", "http_method", "function_name", "relation_type"]) for a in apis])
                    p_dict["apis"] = api_list
                    result_pages.append(p_dict)

        return {"keyword": keyword, "pages": result_pages}

    # ────────────────────────────────────────────────
    # 영향도 분석 (역방향 호출)
    # ────────────────────────────────────────────────
    async def analyze_impact(self, method_full_name: str, depth: int = 5) -> dict[str, Any]:
        """메서드 변경 시 영향받는 상위 호출자 추적 (역방향 BFS)"""
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, method_name, full_name FROM project_methods WHERE full_name = %s LIMIT 1",
                    (method_full_name,),
                )
                target = await cur.fetchone()
                if not target:
                    return {"error": f"메서드를 찾을 수 없습니다: {method_full_name}"}

                callers = await _bfs_call_graph(cur, target[0], direction="backward", max_depth=depth)

        return {
            "target": {"id": target[0], "method_name": target[1], "full_name": target[2]},
            "impacted_callers": callers,
        }

    # ────────────────────────────────────────────────
    # 역방향 추적 (테이블 기준)
    # ────────────────────────────────────────────────
    async def reverse_trace_table(self, table_name: str) -> dict[str, Any]:
        """테이블 기준으로 어떤 화면/API/Mapper가 사용하는지 역추적"""
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                # 테이블 존재 확인
                await cur.execute(
                    "SELECT id, table_name, description FROM project_tables WHERE table_name = %s LIMIT 1",
                    (table_name,),
                )
                table = await cur.fetchone()
                if not table:
                    return {"error": f"테이블을 찾을 수 없습니다: {table_name}"}

                table_id = table[0]

                # 테이블 → SQL
                await cur.execute(
                    """
                    SELECT ps.id, ps.mapper_name, ps.sql_id, ps.sql_type, ps.summary,
                           pst.relation_type, f.file_path
                    FROM project_sql_tables pst
                    JOIN project_sqls ps ON pst.sql_id = ps.id
                    LEFT JOIN project_files f ON ps.file_id = f.id
                    WHERE pst.table_id = %s
                    """,
                    (table_id,),
                )
                sqls = await cur.fetchall()

                # 역방향 graph_node 추적
                await cur.execute(
                    """
                    SELECT gn.node_type, gn.node_name, gn.node_key, gn.summary
                    FROM project_graph_nodes gn
                    WHERE gn.node_key = %s OR gn.node_name = %s
                    LIMIT 1
                    """,
                    (table_name, table_name),
                )
                tbl_node = await cur.fetchone()

                reverse_flow = []
                if tbl_node:
                    reverse_flow = await _traverse_graph(cur, tbl_node[2], direction="backward", max_depth=8)

        return {
            "table": _row_to_dict(table, ["id", "table_name", "description"]),
            "sqls": [_row_to_dict(s, ["id", "mapper_name", "sql_id", "sql_type", "summary", "relation_type", "file_path"]) for s in sqls],
            "reverse_flow": reverse_flow,
        }

    # ────────────────────────────────────────────────
    # 전체 구조 요약
    # ────────────────────────────────────────────────
    async def get_project_structure_summary(self) -> dict[str, Any]:
        """프로젝트 전체 구조 통계 요약"""
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                counts: dict[str, int] = {}
                for tbl in [
                    "project_files", "project_classes", "project_methods",
                    "project_calls", "project_pages", "project_routes",
                    "project_tables", "project_sqls", "project_features",
                    "project_graph_nodes", "project_graph_edges",
                ]:
                    await cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    row = await cur.fetchone()
                    counts[tbl] = row[0] if row else 0

                # class_type별 분류
                await cur.execute(
                    "SELECT class_type, COUNT(*) AS cnt FROM project_classes GROUP BY class_type ORDER BY cnt DESC"
                )
                class_types = await cur.fetchall()

                # node_type별 분류
                await cur.execute(
                    "SELECT node_type, COUNT(*) AS cnt FROM project_graph_nodes GROUP BY node_type ORDER BY cnt DESC"
                )
                node_types = await cur.fetchall()

        return {
            "counts": counts,
            "class_types": [{"type": r[0], "count": r[1]} for r in class_types],
            "node_types": [{"type": r[0], "count": r[1]} for r in node_types],
        }


# ────────────────────────────────────────────────
# 내부 유틸
# ────────────────────────────────────────────────

def _row_to_dict(row: tuple, keys: list[str]) -> dict:
    if row is None:
        return {}
    return dict(zip(keys, row))


async def _bfs_call_graph(cur, start_id: int, direction: str, max_depth: int) -> list[dict]:
    """project_calls 테이블 BFS 순회.
    direction: 'forward' (callee), 'backward' (caller)
    """
    visited = {start_id}
    queue = [(start_id, 0)]
    result = []

    while queue:
        current_id, depth = queue.pop(0)
        if depth >= max_depth:
            continue

        if direction == "forward":
            await cur.execute(
                """
                SELECT m.id, m.method_name, m.full_name, c.class_name, c.class_type, pc.call_type
                FROM project_calls pc
                JOIN project_methods m ON pc.callee_method_id = m.id
                JOIN project_classes c ON m.class_id = c.id
                WHERE pc.caller_method_id = %s
                """,
                (current_id,),
            )
        else:  # backward
            await cur.execute(
                """
                SELECT m.id, m.method_name, m.full_name, c.class_name, c.class_type, pc.call_type
                FROM project_calls pc
                JOIN project_methods m ON pc.caller_method_id = m.id
                JOIN project_classes c ON m.class_id = c.id
                WHERE pc.callee_method_id = %s
                """,
                (current_id,),
            )

        rows = await cur.fetchall()
        for row in rows:
            mid = row[0]
            node = _row_to_dict(row, ["id", "method_name", "full_name", "class_name", "class_type", "call_type"])
            node["depth"] = depth + 1
            result.append(node)
            if mid not in visited:
                visited.add(mid)
                queue.append((mid, depth + 1))

    return result


async def _traverse_graph(cur, start_key: str, direction: str, max_depth: int) -> list[dict]:
    """project_graph_nodes + project_graph_edges BFS 순회"""
    # start node id 조회
    await cur.execute(
        "SELECT id FROM project_graph_nodes WHERE node_key = %s LIMIT 1",
        (start_key,),
    )
    row = await cur.fetchone()
    if not row:
        return []

    start_id = row[0]
    visited = {start_id}
    queue = [(start_id, 0)]
    result = []

    while queue:
        current_id, depth = queue.pop(0)
        if depth >= max_depth:
            continue

        if direction == "forward":
            await cur.execute(
                """
                SELECT gn.id, gn.node_type, gn.node_name, gn.node_key, gn.summary, ge.edge_type
                FROM project_graph_edges ge
                JOIN project_graph_nodes gn ON ge.to_node_id = gn.id
                WHERE ge.from_node_id = %s
                """,
                (current_id,),
            )
        else:  # backward
            await cur.execute(
                """
                SELECT gn.id, gn.node_type, gn.node_name, gn.node_key, gn.summary, ge.edge_type
                FROM project_graph_edges ge
                JOIN project_graph_nodes gn ON ge.from_node_id = gn.id
                WHERE ge.to_node_id = %s
                """,
                (current_id,),
            )

        rows = await cur.fetchall()
        for row in rows:
            nid = row[0]
            node = _row_to_dict(row, ["id", "node_type", "node_name", "node_key", "summary", "edge_type"])
            node["depth"] = depth + 1
            result.append(node)
            if nid not in visited:
                visited.add(nid)
                queue.append((nid, depth + 1))

    return result
