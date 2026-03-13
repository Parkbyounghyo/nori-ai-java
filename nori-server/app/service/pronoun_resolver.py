"""대명사/지시어 해석 및 명사 대체 모듈 (nori용)

myai의 pronoun_resolver.py + question_preprocessor.py에서 핵심 로직 이식.
Java 개발 도우미 맥락에 맞게 도메인 패턴을 코드/기능 도메인으로 교체.
"""
import re
import logging
from typing import Dict, List, Tuple, Any, Optional

logger = logging.getLogger("nori-server")


class PronounResolver:
    """대명사와 지시어를 대화 맥락 기반으로 해석하고 명사로 대체"""

    def __init__(self):
        self.domain_patterns = {
            'board': r'(게시판|board|공지|notice|faq|문의|inq|cooper|제휴)',
            'file_upload': r'(업로드|다운로드|첨부|attach|multipart|파일\s*용량|maxfilesize)',
            'pay': r'(결제|pay|주문|order|카드|pg|refund|환불)',
            'member': r'(회원|member|로그인|login|auth|인증|비밀번호|password)',
            'product': r'(상품|product|장바구니|cart|카탈로그|catalog)',
            'point': r'(포인트|point|쿠폰|coupon|적립|할인)',
            'admin': r'(관리자|admin|어드민|설정|config|환경설정)',
            'batch': r'(배치|batch|스케줄|schedule|cron|자동)',
            'api': r'(api|rest|endpoint|swagger|인터페이스)',
            'security': r'(보안|security|권한|role|xss|csrf|ssl)',
            'db': r'(데이터베이스|db|쿼리|query|sql|mybatis|mapper|테이블|table)',
        }

        self.code_artifact_patterns = {
            'controller': r'(컨트롤러|controller|핸들러|handler)',
            'service': r'(서비스|service|비즈니스|business)',
            'dao': r'(dao|repository|레포지토리|매퍼|mapper)',
            'view': r'(화면|view|jsp|페이지|page|프론트|front|thymeleaf|템플릿)',
            'model': r'(모델|model|dto|vo|entity|엔티티)',
            'config': r'(설정|config|properties|yml|yaml|xml)',
        }

    def resolve(self, current_message: str, history: List[dict]) -> Tuple[str, Dict[str, Any]]:
        """현재 메시지의 대명사/지시어를 히스토리 기반으로 해석

        Returns:
            (해석된_메시지, 메타데이터)
        """
        meta = {
            'replaced': False,
            'pronoun': None,
            'replacement': None,
            'subject_inferred': False,
            'detected_pronouns': [],
        }

        if not current_message or not current_message.strip():
            return current_message, meta

        resolved = current_message

        user_history = self._extract_user_messages(history)

        if self._has_no_domain_subject(current_message) and user_history:
            inferred = self._infer_subject(current_message, user_history)
            if inferred:
                resolved = f"{inferred} {resolved}"
                meta['subject_inferred'] = True
                meta['replacement'] = inferred
                logger.info("[대명사] 주어 유추: '%s' → '%s %s'",
                            current_message[:60], inferred, current_message[:40])

        detected = self._detect_pronouns(resolved)
        meta['detected_pronouns'] = detected

        if not detected:
            return resolved, meta

        pronoun_match = detected[0]
        pronoun = pronoun_match['pronoun']
        pronoun_type = pronoun_match['type']

        replacement = self._find_replacement(pronoun_type, resolved, user_history)

        if replacement and replacement != pronoun:
            resolved = resolved.replace(pronoun, replacement, 1)
            meta['replaced'] = True
            meta['pronoun'] = pronoun
            meta['replacement'] = replacement
            logger.info("[대명사] '%s' → '%s' (type=%s)", pronoun, replacement, pronoun_type)

        return resolved, meta

    def _extract_user_messages(self, history: List[dict]) -> List[str]:
        if not history:
            return []
        return [m.get('content', '') for m in history if m.get('role') == 'user' and m.get('content')]

    def _detect_pronouns(self, text: str) -> List[Dict[str, str]]:
        """텍스트에서 대명사/지시어 감지"""
        detected = []

        domain_related = re.finditer(r'(이것|그것|그거|이거|저것|저거)\s*(관련|에\s*대해|에\s*대한|부분)', text)
        for match in domain_related:
            detected.append({
                'pronoun': match.group(1),
                'type': 'demonstrative_domain',
                'full_phrase': match.group(0),
            })

        for word in ['그것', '그거', '이것', '이거', '저것', '저거']:
            if word in text and not any(d['pronoun'] == word for d in detected):
                detected.append({'pronoun': word, 'type': 'demonstrative'})

        if '거기' in text or '그곳' in text:
            w = '거기' if '거기' in text else '그곳'
            if not any(d['pronoun'] == w for d in detected):
                detected.append({'pronoun': w, 'type': 'place'})

        for word in ['아까', '위에서', '앞에서', '방금']:
            if word in text and not any(d['pronoun'] == word for d in detected):
                detected.append({'pronoun': word, 'type': 'reference_back'})

        if re.search(r'그\s*파일', text):
            detected.append({'pronoun': '그 파일', 'type': 'file_reference'})
        if re.search(r'그\s*부분', text):
            detected.append({'pronoun': '그 부분', 'type': 'code_reference'})
        if re.search(r'그\s*쪽', text):
            detected.append({'pronoun': '그쪽', 'type': 'demonstrative'})

        return detected

    def _find_replacement(self, pronoun_type: str, current: str, user_history: List[str]) -> Optional[str]:
        if not user_history:
            return None

        if pronoun_type in ('demonstrative', 'demonstrative_domain', 'code_reference', 'file_reference'):
            for prev_q in reversed(user_history):
                domain = self._extract_domain_topic(prev_q)
                if domain:
                    return domain

            for prev_q in reversed(user_history):
                artifact = self._extract_code_artifact(prev_q)
                if artifact:
                    return artifact

        if pronoun_type == 'reference_back':
            for prev_q in reversed(user_history):
                domain = self._extract_domain_topic(prev_q)
                if domain:
                    return domain

        return None

    def _extract_domain_topic(self, text: str) -> Optional[str]:
        """텍스트에서 도메인 주제 추출 (예: '게시판', '결제', '회원')"""
        for domain_name, pattern in self.domain_patterns.items():
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return m.group(0)
        return None

    def _extract_code_artifact(self, text: str) -> Optional[str]:
        """텍스트에서 코드 아티팩트 추출 (예: '컨트롤러', 'Service')"""
        for art_name, pattern in self.code_artifact_patterns.items():
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return m.group(0)
        return None

    def _has_no_domain_subject(self, question: str) -> bool:
        """질문에 도메인/코드 주어가 없는지 확인"""
        if len(question.strip()) < 4:
            return True

        for pattern in self.domain_patterns.values():
            if re.search(pattern, question, re.IGNORECASE):
                return False
        for pattern in self.code_artifact_patterns.values():
            if re.search(pattern, question, re.IGNORECASE):
                return False

        has_file_ref = bool(re.search(r'[A-Za-z]\w+\.(java|jsp|xml|properties|yml|sql)', question))
        if has_file_ref:
            return False

        return True

    def _infer_subject(self, question: str, user_history: List[str]) -> Optional[str]:
        """히스토리에서 주어(도메인/아티팩트) 유추"""
        action_only = any(v in question for v in (
            '알려', '설명', '보여', '찾아', '어떻게', '수정', '추가', '삭제',
            '리팩토링', '개선', '자세히', '더', '마저', '계속',
        ))
        if not action_only and len(question.strip()) > 8:
            return None

        for prev_q in reversed(user_history):
            domain = self._extract_domain_topic(prev_q)
            if domain:
                return domain
            artifact = self._extract_code_artifact(prev_q)
            if artifact:
                return artifact

        return None


class QueryCleaner:
    """질문 정규화 — 불필요한 표현 제거 (myai query_cleaner.py 경량 이식)"""

    FILLER_WORDS = [
        r'좀\s*', r'우선\s*', r'일단\s*', r'혹시\s*',
        r'부탁\s*', r'한\s*번\s*', r'빨리\s*',
    ]

    REQUEST_ENDINGS = [
        r'해\s*줄\s*래\s*\?*', r'해\s*줘\s*\?*', r'해\s*주세요\s*\?*',
        r'알려\s*줘\s*\?*', r'알려\s*주세요\s*\?*',
        r'찾아\s*줘\s*\?*', r'찾아\s*주세요\s*\?*',
        r'보여\s*줘\s*\?*',
    ]

    EMOTION_PATTERNS = [
        r'ㅋ+', r'ㅎ+', r'ㅠ+', r'ㅜ+',
    ]

    JOSA_PATTERNS = [
        (r'([가-힣]+)이라서', r'\1'),
        (r'([가-힣]+)니까', r'\1'),
        (r'([가-힣]+)이니까', r'\1'),
    ]

    def clean(self, query: str) -> Tuple[str, bool]:
        """질문 정규화

        Returns:
            (정제된 질문, 변경 여부)
        """
        original = query

        for pattern in self.EMOTION_PATTERNS:
            query = re.sub(pattern, ' ', query)

        for pattern in self.FILLER_WORDS:
            query = re.sub(pattern, ' ', query, flags=re.IGNORECASE)

        for pattern in self.REQUEST_ENDINGS:
            query = re.sub(pattern, '', query, flags=re.IGNORECASE)

        for pattern, replacement in self.JOSA_PATTERNS:
            query = re.sub(pattern, replacement, query)

        query = re.sub(r'\s+', ' ', query).strip()
        query = re.sub(r'\?+$', '', query).strip()

        changed = (original.strip() != query)
        if changed:
            logger.debug("[QueryCleaner] '%s' → '%s'", original[:80], query[:80])

        return query, changed
