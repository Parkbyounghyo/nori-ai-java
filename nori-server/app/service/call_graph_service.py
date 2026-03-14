"""
Call Graph Service — Full Stack Flow 생성기

project_graph_nodes / project_graph_edges 기반으로
PAGE → JS → API → Controller → Service → Mapper → SQL → Table
전체 흐름을 조립 및 포맷 출력
"""
import logging
from typing import Any

from app.db.connection import get_conn
from app.service.structure_search_service import _row_to_dict, _traverse_graph

logger = logging.getLogger("nori-server")


class CallGraphService:

    # ────────────────────────────────────────────────
    # 기능명으로 Full Stack Flow 조회
    # ────────────────────────────────────────────────
    async def get_feature_flow(self, feature_keyword: str) -> dict[str, Any]:
        """기능명 키워드로 Full Stack Flow 조회"""
        like = f"%{feature_keyword}%"
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                # feature 인덱스 검색
                await cur.execute(
                    """
                    SELECT f.id, f.feature_name, f.description,
                           f.entry_point_type, f.entry_point_id
                    FROM project_features f
                    WHERE f.feature_name LIKE %s OR f.description LIKE %s
                    LIMIT 3
                    """,
                    (like, like),
                )
                features = await cur.fetchall()

                result = []
                for feat in features:
                    feat_id = feat[0]
                    feat_dict = _row_to_dict(feat, ["id", "feature_name", "description", "entry_point_type", "entry_point_id"])

                    # feature_nodes로 흐름 조립
                    await cur.execute(
                        """
                        SELECT fn.node_type, fn.node_id, fn.sort_order,
                               gn.node_name, gn.node_key, gn.summary
                        FROM project_feature_nodes fn
                        LEFT JOIN project_graph_nodes gn ON fn.node_id = gn.id
                        WHERE fn.feature_id = %s
                        ORDER BY fn.sort_order ASC
                        """,
                        (feat_id,),
                    )
                    nodes = await cur.fetchall()
                    feat_dict["flow_nodes"] = [
                        _row_to_dict(n, ["node_type", "node_id", "sort_order", "node_name", "node_key", "summary"])
                        for n in nodes
                    ]
                    result.append(feat_dict)

        return {"keyword": feature_keyword, "features": result}

    # ────────────────────────────────────────────────
    # 특정 graph_node에서 Full Stack 정방향 탐색
    # ────────────────────────────────────────────────
    async def trace_full_stack(self, node_key: str) -> dict[str, Any]:
        """graph_node key 기준 Full Stack Flow 정방향 탐색"""
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, node_type, node_name, node_key, summary
                    FROM project_graph_nodes
                    WHERE node_key = %s
                    LIMIT 1
                    """,
                    (node_key,),
                )
                start_node = await cur.fetchone()
                if not start_node:
                    return {"error": f"노드를 찾을 수 없습니다: {node_key}"}

                start_dict = _row_to_dict(start_node, ["id", "node_type", "node_name", "node_key", "summary"])
                flow = await _traverse_graph(cur, node_key, direction="forward", max_depth=10)

        formatted = _format_full_stack_flow(start_dict, flow)
        return {
            "start_node": start_dict,
            "raw_flow": flow,
            "formatted_flow": formatted,
        }

    # ────────────────────────────────────────────────
    # 노드 검색 (type + keyword)
    # ────────────────────────────────────────────────
    async def find_graph_nodes(self, node_type: str | None, keyword: str) -> list[dict]:
        """그래프 노드 검색 — type 필터 + keyword"""
        like = f"%{keyword}%"
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                if node_type:
                    await cur.execute(
                        """
                        SELECT id, node_type, node_name, node_key, ref_table, summary
                        FROM project_graph_nodes
                        WHERE node_type = %s AND (node_name LIKE %s OR node_key LIKE %s OR summary LIKE %s)
                        LIMIT 20
                        """,
                        (node_type.upper(), like, like, like),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, node_type, node_name, node_key, ref_table, summary
                        FROM project_graph_nodes
                        WHERE node_name LIKE %s OR node_key LIKE %s OR summary LIKE %s
                        LIMIT 20
                        """,
                        (like, like, like),
                    )
                rows = await cur.fetchall()

        return [_row_to_dict(r, ["id", "node_type", "node_name", "node_key", "ref_table", "summary"]) for r in rows]

    # ────────────────────────────────────────────────
    # 노드 간 경로 찾기
    # ────────────────────────────────────────────────
    async def find_path(self, from_key: str, to_key: str) -> dict[str, Any]:
        """두 노드 간 최단 경로 탐색 (BFS)"""
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, node_type, node_name FROM project_graph_nodes WHERE node_key = %s LIMIT 1",
                    (from_key,),
                )
                from_node = await cur.fetchone()
                await cur.execute(
                    "SELECT id, node_type, node_name FROM project_graph_nodes WHERE node_key = %s LIMIT 1",
                    (to_key,),
                )
                to_node = await cur.fetchone()

                if not from_node or not to_node:
                    return {"error": "시작 또는 도착 노드를 찾을 수 없습니다"}

                path = await _bfs_path(cur, from_node[0], to_node[0], max_depth=10)

        return {
            "from": _row_to_dict(from_node, ["id", "node_type", "node_name"]),
            "to": _row_to_dict(to_node, ["id", "node_type", "node_name"]),
            "path": path,
        }

    # ────────────────────────────────────────────────
    # 검색 응답 포맷 생성
    # ────────────────────────────────────────────────
    def build_search_response(self, feature_name: str, flow_data: dict) -> str:
        """컨셉변경.md 9장 포맷으로 검색 응답 텍스트 생성"""
        lines = [f"기능: {feature_name}", ""]
        features = flow_data.get("features", [])
        if not features:
            return f"'{feature_name}' 기능을 찾을 수 없습니다."

        feat = features[0]
        nodes = feat.get("flow_nodes", [])

        pages, scripts, apis, controllers, services, mappers, sqls, tables = [], [], [], [], [], [], [], []
        for n in nodes:
            nt = n.get("node_type", "")
            nm = n.get("node_name", n.get("node_key", ""))
            if nt == "PAGE":
                pages.append(nm)
            elif nt == "JS":
                scripts.append(nm)
            elif nt == "API":
                apis.append(nm)
            elif nt == "CONTROLLER":
                controllers.append(nm)
            elif nt == "SERVICE":
                services.append(nm)
            elif nt == "MAPPER":
                mappers.append(nm)
            elif nt == "SQL":
                sqls.append(nm)
            elif nt == "TABLE":
                tables.append(nm)

        if pages:
            lines.append("관련 페이지")
            lines.extend(f"- {p}" for p in pages)
            lines.append("")
        if scripts:
            lines.append("JS 파일")
            lines.extend(f"- {s}" for s in scripts)
            lines.append("")
        if apis:
            lines.append("진입 URL")
            lines.extend(f"- {a}" for a in apis)
            lines.append("")
        if controllers or services or mappers:
            lines.append("처리 클래스")
            lines.extend(f"- {c}" for c in controllers + services + mappers)
            lines.append("")
        if sqls:
            lines.append("관련 SQL")
            lines.extend(f"- {s}" for s in sqls)
            lines.append("")
        if tables:
            lines.append("관련 DB")
            lines.extend(f"- {t}" for t in tables)
            lines.append("")

        # 처리 흐름
        all_nodes = pages + scripts + apis + controllers + services + mappers + sqls + tables
        if all_nodes:
            lines.append("처리 흐름")
            lines.append("\n→ ".join(all_nodes))

        return "\n".join(lines)


# ────────────────────────────────────────────────
# 내부 유틸
# ────────────────────────────────────────────────

# node_type 순서 (Full Stack 레이어)
_LAYER_ORDER = {
    "PAGE": 0, "JS": 1, "API": 2,
    "CONTROLLER": 3, "SERVICE": 4, "MAPPER": 5,
    "SQL": 6, "TABLE": 7,
    "CLASS": 3, "METHOD": 4, "FEATURE": 0,
}


def _format_full_stack_flow(start: dict, flow: list[dict]) -> str:
    """Full Stack 흐름을 레이어 순서에 맞게 텍스트로 포맷"""
    all_nodes = [start] + flow
    # 레이어 순 정렬 (같은 레이어는 depth 순)
    all_nodes.sort(key=lambda n: (_LAYER_ORDER.get(n.get("node_type", ""), 99), n.get("depth", 0)))

    lines = []
    prev_type = None
    for n in all_nodes:
        nt = n.get("node_type", "")
        nm = n.get("node_name", n.get("node_key", "?"))
        edge = n.get("edge_type", "")

        if lines and nt != prev_type:
            edge_label = edge if edge else "↓"
            lines.append(f" {edge_label}")
        lines.append(nm)
        prev_type = nt

    return "\n".join(lines)


async def _bfs_path(cur, from_id: int, to_id: int, max_depth: int) -> list[dict]:
    """BFS로 두 graph_node간 최단 경로 탐색"""
    if from_id == to_id:
        return []

    visited = {from_id: None}  # node_id → parent_id
    queue = [(from_id, 0)]

    while queue:
        current_id, depth = queue.pop(0)
        if depth >= max_depth:
            continue

        await cur.execute(
            """
            SELECT gn.id, gn.node_type, gn.node_name, gn.node_key, ge.edge_type
            FROM project_graph_edges ge
            JOIN project_graph_nodes gn ON ge.to_node_id = gn.id
            WHERE ge.from_node_id = %s
            """,
            (current_id,),
        )
        rows = await cur.fetchall()
        for row in rows:
            nid = row[0]
            if nid not in visited:
                visited[nid] = current_id
                if nid == to_id:
                    # 경로 역추적
                    path = []
                    cur_id = to_id
                    while cur_id is not None:
                        await cur.execute(
                            "SELECT id, node_type, node_name, node_key FROM project_graph_nodes WHERE id = %s",
                            (cur_id,),
                        )
                        n = await cur.fetchone()
                        if n:
                            path.append(_row_to_dict(n, ["id", "node_type", "node_name", "node_key"]))
                        cur_id = visited.get(cur_id)
                    path.reverse()
                    return path
                queue.append((nid, depth + 1))

    return []  # 경로 없음
