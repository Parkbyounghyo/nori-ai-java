# 파일 선별 다중 질문 테스트 보고

**실행일**: 2026-03-11  
**프로필**: gpoint 스타일 (Board, FAQ, Approval, Event 등 14개 경로)

---

## 요약

| # | 질문 | 선별 수 | 평가 | 비고 |
|---|------|:------:|:----:|------|
| 1 | faq 게시판에 메일 항목 추가 | 4 | ✅ | Faq, CooperInq, JSP만 — 도메인 필터 정상 |
| 2 | faq 게시판 메일 추가 (쓰레기 LLM) | 4 | ✅ | "등 프로필" 무시, 동일 결과 |
| 3 | 공지 게시판 검색 필드 추가 | 13 | ⚠️ | 과다 확장 (Faq/Approval/Event 포함) |
| 4 | 승인 관리 화면 수정 | 10 | ⚠️ | Approval 정확, 나머지 과다 |
| 5 | 제휴 문의 전화번호 추가 | 5 | ✅ | CooperInq 중심, FAQ 관련 포함 적절 |
| 6 | 게시판 이메일 필드 추가 | 14 | ⚠️ | Board+Mail 맞음, 전체 과다 |
| 7 | 포인트 적립 로직 어떻게 돼? | 13 | ⚠️ | Pay 없음 → 키워드 확장으로 전부 |
| 8 | BoardServiceImpl 코드 설명 | 10 | ⚠️ | 1개 요구인데 10개 — 과다 확장 |

---

## 상세 결과

### ✅ 양호 (FAQ·제휴 문의)
- **질문 1, 2**: FAQ+메일 → FaqController, faqList, CooperInqController, faqForm
- **질문 5**: 제휴 문의 → CooperInqController, cooperInqView + FAQ 관련

### ⚠️ 과다 확장 (공지·승인·게시판·설명)
- **질문 3 (공지)**: BoardController, BoardService로 시작하지만, 기능 요약 블록 "게시판 관리"에 FAQ가 같이 있어 전부 포함
- **질문 4 (승인)**: Approval* 정확, 동일 블록 확장으로 나머지 포함
- **질문 6 (게시판 이메일)**: Board+BoardMailVO 맞으나 블록 확장으로 전부
- **질문 8 (설명)**: 단일 파일 요청인데 동일 패키지/블록 확장으로 10개

### 키워드 검색(폴백)
| 질문 | 결과 |
|------|------|
| faq 메일 추가 | faqForm, faqList, FaqController, BoardMailVO ✅ |
| 승인 관리 | ApprovalController, ApprovalServiceImpl, mybatis_sql_admin_approval.xml ✅ |
| 이벤트 목록 수정 | EventController ✅ |
| 자유게시판 | Board*, mybatis_sql_admin_board ✅ |

---

## 권장 조치

1. **FAQ 도메인 한정**: 정상 동작
2. **승인 키워드**: `"승인": "approval"` 추가 완료
3. **과다 확장**: 공지/승인/게시판 일반 질문 시 기능 블록 확장이 전 도메인을 포함함. 질문 도메인(공지→Board만, 승인→Approval만) 한정 확장 검토 시 유의
