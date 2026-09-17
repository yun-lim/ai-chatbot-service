# static/vendor — 외부 라이브러리 (손대지 않는다)

이 프로젝트에는 빌드 단계가 없다. 그래서 브라우저용 배포 파일을 **그대로** 넣고 우리 서버가 준다 — CDN 이 막혀도
답변 화면이 깨지지 않는다. 파일 맨 위의 라이선스 배너를 지우지 않는다.

| 파일 | 패키지 · 버전 | 라이선스 | 출처 | SHA-256 |
|---|---|---|---|---|
| `marked.umd.js` | marked 18.0.13 | MIT | npm `marked@18.0.13` 의 `lib/marked.umd.js` | `b147274a9ce27d17276587167e49483d719f6893eeca3a3667a59797661d3556` |
| `purify.min.js` | DOMPurify 3.4.15 | Apache-2.0 / MPL-2.0 | npm `dompurify@3.4.15` 의 `dist/purify.min.js` | `f263b05369e050fa175d4ecb9c9358eb4253602d510297adfb31df48b2f1c4d5` |

두 파일 모두 npm 레지스트리의 tarball 안의 파일과 해시가 같은 것을 확인하고 넣었다 (#172).
`tests/test_markdown_js.py` 가 위 해시와 실제 파일을 대조한다 — 파일이 바뀌면 테스트가 실패한다.

## 버전을 올릴 때

```bash
curl -sSfL -o marked.umd.js  https://cdn.jsdelivr.net/npm/marked@<버전>/lib/marked.umd.js
curl -sSfL -o purify.min.js  https://cdn.jsdelivr.net/npm/dompurify@<버전>/dist/purify.min.js
shasum -a 256 marked.umd.js purify.min.js     # 위 표의 버전과 해시를 함께 고친다
```

쓰는 곳은 `static/markdown.js` 하나다. `marked` 는 원문 HTML 을 그대로 통과시키므로 **반드시** `DOMPurify` 를 거친다.
