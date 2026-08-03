# assets/

PRD §8이 정의한 에셋 위치다. **파일이 없어도 게임은 정상 동작한다** — 배경은 분야별 CSS 그라디언트로, 소리는 Web Audio 합성음으로 자동 대체된다. 아래 이름 그대로 파일을 넣으면 코드 수정 없이 그때부터 파일이 쓰인다.

```
assets/
  bg/
    start.jpg              # 시작·완료·랭킹 화면 배경
    korean_history.jpg     # 궁궐/한옥/고서
    science.jpg            # 실험실/우주/분자
    world_geography.jpg    # 지구본/세계지도
    arts_culture.jpg       # 미술관/공연/도시
  bgm/
    travel1.mp3 ~ travel4.mp3   # 세계여행풍 경쾌 BGM (분야 전환마다 순환)
  sfx/
    correct.mp3            # 정답 효과음
    wrong.mp3              # 오답 효과음
```

## 판별 규칙

- **배경**: `views.js`가 `assets/bg/<key>.jpg`를 `Image()`로 로드해 보고, 성공하면 이미지로 교체한다. 확장자는 `.jpg` 고정이다.
- **BGM**: `audio.js`가 네 파일에 `HEAD` 요청을 보내 **하나라도 있으면 파일 모드**, 전부 없으면 합성 모드로 간다. 일부만 넣으면 없는 트랙 차례에 소리가 끊기므로 넣으려면 4개를 다 넣을 것.
- **효과음**: `correct`/`wrong`을 각각 독립적으로 판별한다. 한쪽만 넣어도 된다.

에셋은 **로열티 프리**인 것만 사용할 것.
