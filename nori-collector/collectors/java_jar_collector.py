# java_jar_collector.py
"""
자바 jar 파일 자동 수집 collector 템플릿
- Maven Central, javadoc, 공식 repo 등에서 jar 메타데이터/설명/사용법/예제 자동 수집
- data/jar/, data/javadoc/, data/usage/ 등으로 저장
- 주기적 업데이트(스케줄링) 지원
"""

import requests
import os
import json
from datetime import datetime

JAR_SITES = [
    'https://search.maven.org/',
    'https://repo1.maven.org/maven2/',
    # 추가 사이트 필요시 여기에
]

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'jar')

class JavaJarCollector:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    def fetch_jar_metadata(self, group_id, artifact_id, version):
        # Maven Central API 예시
        url = f'https://search.maven.org/solrsearch/select?q=g:{group_id}+AND+a:{artifact_id}+AND+v:{version}&rows=1&wt=json'
        resp = requests.get(url)
        if resp.status_code == 200:
            return resp.json()
        return None

    def save_metadata(self, jar_name, metadata):
        path = os.path.join(DATA_DIR, f'{jar_name}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    def collect(self, jar_list):
        for jar in jar_list:
            group_id, artifact_id, version = jar
            meta = self.fetch_jar_metadata(group_id, artifact_id, version)
            if meta:
                jar_name = f'{artifact_id}-{version}'
                self.save_metadata(jar_name, meta)
                print(f'수집 완료: {jar_name}')
            else:
                print(f'수집 실패: {artifact_id}-{version}')

    def schedule_update(self):
        # TODO: 스케줄링 구현 (예: cron, timer 등)
        pass

if __name__ == '__main__':
    # 예시 jar 리스트
    jars = [
        ('org.apache.commons', 'commons-lang3', '3.12.0'),
        ('com.google.guava', 'guava', '31.1-jre'),
    ]
    collector = JavaJarCollector()
    collector.collect(jars)
