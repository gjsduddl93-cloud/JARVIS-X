import json

d = json.load(open("data/viral_patterns.json", encoding="utf-8"))
print(f"  버전: v{d.get('version', 1)}")
print(f"  업데이트: {d.get('updated_at', '')}")
print(f"  패턴 수: {len(d.get('title_patterns', []))}개")
print(f"  키워드 수: {len(d.get('trending_keywords', []))}개")
print(f"  오늘 프롬프트: {d.get('today_prompt', '')[:60]}...")
