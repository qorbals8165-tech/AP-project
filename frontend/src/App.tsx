import { ChangeEvent, CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAudioDevices,
  fetchDefaults,
  fetchHealth,
  fetchRecognitionState,
  fetchSystemFonts,
  importDocument,
  startRecognition,
  stopRecognition,
  updateRecognitionScript,
  type AudioDevice,
} from "./api";
import {
  buildLineRanges,
  firstNonEmptyFrom,
  firstNonEmptyLineIndex,
  lineIndexForCharIndex,
  lineMatchesSpoken,
  nextNonEmptyLineIndex,
} from "./lineMatch";
import type { HealthResponse, ThemeName, UiSettings } from "./types";

const DEFAULT_SCRIPT = `안녕하세요. 이 텔레프롬프터는 Whisper 기반 음성 인식을 통해 대사를 자동으로 진행합니다.
사용자는 키워드 탐색으로 원하는 문단으로 빠르게 점프할 수 있습니다.
자동 스크롤 기능과 사용자 인터페이스 설정을 통해 발표 환경에 맞게 조정할 수 있습니다.
GPU 가속이 가능한 환경에서는 더 빠른 추론 성능을 기대할 수 있습니다.
이 프로젝트는 실제 사용 가능한 AI 텔레프롬프터 애플리케이션을 목표로 합니다.`;

const THEMES: Record<ThemeName, { label: string; className: string }> = {
  studio: { label: "Studio", className: "theme-studio" },
  stage: { label: "Stage", className: "theme-stage" },
  paper: { label: "Paper", className: "theme-paper" },
};

// UI/본문 기본 글꼴 — 가독성 좋은 표준 시스템 폰트
const DEFAULT_FONT = "system-ui, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', 'Segoe UI', sans-serif";
const PAPERLOGY_FONT = "'Paperlogy', sans-serif";

// 인식 텍스트가 현재 줄과 매칭된 뒤 다음 줄로 넘기기까지의 지연(ms)
const ADVANCE_DELAY_MS = 700;

const POLL_MS = 150;

export default function App() {
  const [mode, setMode] = useState<"edit" | "present">("edit");
  const [script, setScript] = useState(DEFAULT_SCRIPT);
  const [keyword, setKeyword] = useState("");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState("");
  const [currentLineIndex, setCurrentLineIndex] = useState(0);
  const [autoLineAdvance, setAutoLineAdvance] = useState(true);
  const [settings, setSettings] = useState<UiSettings>({
    fontSize: 36,
    lineHeight: 1.55,
    scrollSpeed: 28,
    contentWidth: 900,
    theme: "studio",
    fontFamily: DEFAULT_FONT,
    fontWeight: 400,
    textColor: "#ffffff",
    bgColor: "#1b1410",
  });
  const [audioDevices, setAudioDevices] = useState<AudioDevice[]>([]);
  const [selectedDeviceIndex, setSelectedDeviceIndex] = useState<number | null>(null);
  const [inputLevel, setInputLevel] = useState(0);
  const [peakLevel, setPeakLevel] = useState(0);
  const [isContinuousMode, setIsContinuousMode] = useState(false);
  const [recognizedText, setRecognizedText] = useState("");
  const [importStatus, setImportStatus] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [localFonts, setLocalFonts] = useState<string[]>([]);

  const modeRef = useRef<"edit" | "present">("edit");
  const teleprompterRef = useRef<HTMLDivElement | null>(null);
  const lineRefs = useRef<(HTMLParagraphElement | null)[]>([]);
  const peakTimerRef = useRef<number>(0);
  const pollTimerRef = useRef<number>(0);
  const lastSeqRef = useRef<number>(0);
  const linesRef = useRef<string[]>([]);
  const currentLineIndexRef = useRef(0);
  const autoLineAdvanceRef = useRef(autoLineAdvance);
  const advanceTimerRef = useRef<number>(0);

  const lines = useMemo(() => script.split(/\r?\n/), [script]);

  useEffect(() => { modeRef.current = mode; }, [mode]);
  useEffect(() => { linesRef.current = lines; }, [lines]);
  useEffect(() => { currentLineIndexRef.current = currentLineIndex; }, [currentLineIndex]);
  useEffect(() => { autoLineAdvanceRef.current = autoLineAdvance; }, [autoLineAdvance]);

  // ── 장치 목록 (네이티브, 권한 팝업 없음) ─────────────────────────────────

  const loadDevices = useCallback(async () => {
    try {
      const devs = await fetchAudioDevices();
      setAudioDevices(devs);
      setSelectedDeviceIndex((prev) => {
        if (prev !== null && devs.some((d) => d.index === prev)) return prev;
        const def = devs.find((d) => d.default) ?? devs[0];
        return def ? def.index : null;
      });
    } catch {
      // 무시
    }
  }, []);

  useEffect(() => {
    setCurrentLineIndex(firstNonEmptyLineIndex(lines));
  }, [script]);

  useEffect(() => {
    // 테마는 studio로 고정
    void fetchDefaults().then((s) => setSettings({ ...s, theme: "studio" })).catch(() => undefined);
    void fetchHealth().then(setHealth).catch(() => undefined);
    void loadDevices();
    void loadSystemFonts(); // 설치된 폰트를 미리 불러와 글꼴 목록 채움
  }, [loadDevices]);

  useEffect(() => {
    const el = lineRefs.current[currentLineIndex];
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [currentLineIndex]);

  const lineStats = useMemo(() => {
    const readable = lines.filter((l) => l.trim().length > 0).length;
    const currentNum =
      lines.slice(0, currentLineIndex + 1).filter((l) => l.trim().length > 0).length;
    return { readable, currentNum };
  }, [lines, currentLineIndex]);

  // ── 줄 진행 ────────────────────────────────────────────────────────────

  function advanceLineFromTranscript(text: string) {
    if (!autoLineAdvanceRef.current) return;
    if (advanceTimerRef.current) return; // 이미 넘김이 예약됨
    const ls = linesRef.current;
    const cur = currentLineIndexRef.current;
    // 빈 줄에서는 음성으로 넘기지 않음 — 방향키(↓)로만 다음 줄 이동
    if (!ls[cur] || ls[cur].trim().length === 0) return;
    if (!lineMatchesSpoken(ls[cur], text)) return;
    // 매칭되면 살짝 지연 후 바로 다음 줄로 (빈 줄도 멈춤 지점이 됨)
    advanceTimerRef.current = window.setTimeout(() => {
      advanceTimerRef.current = 0;
      setCurrentLineIndex((prev) => Math.min(prev + 1, linesRef.current.length - 1));
    }, ADVANCE_DELAY_MS);
  }

  // 현재 줄 + 다음 줄을 Whisper 힌트로 사용
  function currentHint(): string {
    const ls = linesRef.current;
    const li = firstNonEmptyFrom(ls, currentLineIndexRef.current);
    const next = nextNonEmptyLineIndex(ls, li);
    return [ls[li], ls[next]].filter(Boolean).join(" ");
  }

  // ── 폴링 (레벨 + 인식 텍스트) ────────────────────────────────────────────

  const startPolling = useCallback(() => {
    if (pollTimerRef.current) return;
    pollTimerRef.current = window.setInterval(async () => {
      try {
        const st = await fetchRecognitionState();
        setInputLevel(st.level);
        setPeakLevel((prev) => {
          if (st.level > prev) {
            clearTimeout(peakTimerRef.current);
            peakTimerRef.current = window.setTimeout(() => setPeakLevel(0), 1500);
            return st.level;
          }
          return prev;
        });
        if (st.seq !== lastSeqRef.current) {
          lastSeqRef.current = st.seq;
          if (st.text) {
            setRecognizedText(st.text);
            advanceLineFromTranscript(st.text);
          }
        }
      } catch {
        // 폴링 실패 무시
      }
    }, POLL_MS);
  }, []);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = 0;
    }
    if (advanceTimerRef.current) {
      clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = 0;
    }
    clearTimeout(peakTimerRef.current);
    setInputLevel(0);
    setPeakLevel(0);
  }, []);

  // 발표 모드 인식 ON/OFF 토글 (실제 시작·중지는 아래 effect가 담당)
  function toggleContinuousMode() {
    setIsContinuousMode((v) => !v);
  }

  // ── 단일 인식 라이프사이클 ────────────────────────────────────────────────
  // 편집(레벨만)·발표(전사) 두 경우를 하나의 effect로 관리한다. start/stop을
  // 서로 다른 effect가 동시에 호출하면 경쟁(race)으로 인식이 즉시 꺼질 수 있어,
  // 항상 "원하는 상태"를 단일 호출로 적용한다.
  useEffect(() => {
    if (selectedDeviceIndex === null) return;
    const transcribe = mode === "present" && isContinuousMode;
    const active = mode === "edit" || transcribe;
    let cancelled = false;
    (async () => {
      try {
        if (!active) {
          stopPolling();
          await stopRecognition().catch(() => undefined);
          return;
        }
        lastSeqRef.current = 0;
        setError("");
        // startRecognition은 백엔드에서 기존 스트림을 멈추고 새로 시작(원자적)
        await startRecognition(selectedDeviceIndex, { transcribe, script: currentHint() });
        if (cancelled) return;
        startPolling();
      } catch (err) {
        if (transcribe) {
          setError(err instanceof Error ? err.message : "마이크를 시작할 수 없습니다.");
          setIsContinuousMode(false);
        }
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, isContinuousMode, selectedDeviceIndex]);

  // 언마운트 시 인식 정리
  useEffect(() => {
    return () => {
      stopPolling();
      void stopRecognition().catch(() => undefined);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 연속 인식 중 줄이 바뀌면 힌트 갱신
  useEffect(() => {
    if (mode === "present" && isContinuousMode) void updateRecognitionScript(currentHint());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentLineIndex, isContinuousMode, mode]);

  // ── 문서 가져오기 ────────────────────────────────────────────────────────

  async function handleDocumentImport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setIsImporting(true);
    setImportStatus("불러오는 중…");
    try {
      const text = await importDocument(file);
      setScript(text);
      const lineCount = text.split("\n").filter((l) => l.trim()).length;
      setImportStatus(`✓ ${file.name} (${lineCount}줄)`);
      setTimeout(() => setImportStatus(""), 5000);
    } catch (err) {
      setImportStatus(`오류: ${err instanceof Error ? err.message : "알 수 없는 오류"}`);
    } finally {
      setIsImporting(false);
      event.target.value = "";
    }
  }

  // ── 글꼴 (시스템 폰트) ────────────────────────────────────────────────────

  // 백엔드가 OS 폰트 폴더를 스캔해 설치된 폰트 패밀리명을 돌려준다.
  // (브라우저 Local Font Access API와 달리 macOS WKWebView에서도 동작)
  async function loadSystemFonts() {
    try {
      const families = await fetchSystemFonts();
      if (families.length === 0) {
        setError("설치된 폰트를 찾지 못했습니다.");
        return;
      }
      setLocalFonts(families);
      setError("");
    } catch {
      setError("PC 폰트 목록을 불러오지 못했습니다.");
    }
  }

  // ── 키워드 점프 ────────────────────────────────────────────────────────────

  function jumpToKeyword() {
    if (!keyword.trim()) return;
    const index = script.toLowerCase().indexOf(keyword.trim().toLowerCase());
    if (index < 0) { setError("키워드를 찾지 못했습니다."); return; }
    setError("");
    const ranges = buildLineRanges(script);
    setCurrentLineIndex(lineIndexForCharIndex(ranges, index));
  }

  // ── 방향키 네비게이션 ────────────────────────────────────────────────────

  const goToPreviousLine = useCallback(() => {
    setCurrentLineIndex((prev) => Math.max(0, prev - 1));
  }, []);

  const goToNextLineManual = useCallback(() => {
    setCurrentLineIndex((prev) => Math.min(linesRef.current.length - 1, prev + 1));
  }, []);

  // 화면 전환 — 인식 시작·중지는 mode/isContinuousMode effect가 담당
  function goToEdit() {
    setMode("edit");
  }

  function goToPresent() {
    setIsContinuousMode(true); // 발표 진입 시 음성 인식 자동 활성화
    setMode("present");
  }

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (modeRef.current !== "present") return;
      const target = e.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (e.key === "ArrowDown") { e.preventDefault(); goToNextLineManual(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); goToPreviousLine(); }
      else if (e.key === " ") { e.preventDefault(); void toggleContinuousMode(); }
      else if (e.key === "Escape") { e.preventDefault(); goToEdit(); }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goToNextLineManual, goToPreviousLine]);

  // ── 텔레프롬프터 라인 렌더링 ──────────────────────────────────────────────

  const prompterLines = lines.map((line, index) => {
    const trimmed = line.trim().length > 0;
    const isCurrent = index === currentLineIndex;
    const isPast = trimmed && index < currentLineIndex;
    const distance = Math.abs(index - currentLineIndex);
    const distStr = trimmed && !isCurrent ? ` dist-${Math.min(distance, 5)}` : "";
    const lineClass = isCurrent
      ? trimmed
        ? "prompter-line line-current"
        : "prompter-line line-empty line-current"
      : !trimmed
        ? "prompter-line line-empty"
        : isPast
          ? `prompter-line line-done${distStr}`
          : `prompter-line line-upcoming${distStr}`;
    return (
      <p
        key={`line-${index}`}
        ref={(el) => { lineRefs.current[index] = el; }}
        className={lineClass}
      >
        {line || " "}
      </p>
    );
  });

  // VU 미터 (가로, 24 세그먼트)
  function renderVuMeter() {
    const SEGS = 24;
    const peakIdx = Math.min(SEGS - 1, Math.max(0, Math.ceil((peakLevel / 100) * SEGS) - 1));
    return (
      <div className="vu-h">
        {Array.from({ length: SEGS }, (_, i) => {
          const lit = inputLevel > (i / SEGS) * 100;
          const isPeak = i === peakIdx && !lit && peakLevel > 0;
          const color = i >= 20 ? "vu-r" : i >= 15 ? "vu-y" : "vu-g";
          return (
            <div key={i} className={`vu-seg ${color}${lit ? " lit" : ""}${isPeak ? " peak" : ""}`} />
          );
        })}
      </div>
    );
  }

  // ── 발표 화면 ────────────────────────────────────────────────────────────

  if (mode === "present") {
    return (
      <div
        className={`app-shell ${THEMES[settings.theme].className}`}
        style={{ background: settings.bgColor }}
      >
        <div
          ref={teleprompterRef}
          className="teleprompter-view"
          style={
            {
              "--font-size": `${settings.fontSize}px`,
              "--line-height": String(settings.lineHeight),
              "--content-width": `${settings.contentWidth}px`,
              "--font-family": settings.fontFamily,
              "--font-weight": String(settings.fontWeight),
              "--text-color": settings.textColor,
            } as CSSProperties
          }
        >
          <div className="teleprompter-gradient top" />
          <div className="teleprompter-content">{prompterLines}</div>
          <div className="teleprompter-gradient bottom" />
        </div>

        {isContinuousMode && (
          <div className="level-bar-overlay">
            <div className="level-bar-fill" style={{ width: `${inputLevel}%` }} />
          </div>
        )}

        <div className="present-hud">
          <div className="hud-left">
            <button className="hud-back-btn" onClick={goToEdit} title="편집 화면으로 돌아가기 (Esc)">
              ← 편집
            </button>
            <span className="hud-line-counter">
              {lineStats.currentNum} / {lineStats.readable || "—"}
            </span>
            {isContinuousMode
              ? <span className="hud-mode">🎙 인식 중 (Space로 끄기)</span>
              : <span className="hud-mode hud-off">⏸ 인식 꺼짐 (Space로 켜기)</span>}
            {error && <span className="hud-error">{error}</span>}
          </div>
        </div>

        {/* 인식된 텍스트 미리보기 (디버그/확인용) */}
        {isContinuousMode && recognizedText && (
          <div className="recognized-overlay">{recognizedText}</div>
        )}
      </div>
    );
  }

  // ── 편집 화면 ────────────────────────────────────────────────────────────

  return (
    <div className={`edit-shell ${THEMES[settings.theme].className}`}>
      <header className="edit-header">
        <h1 className="edit-app-title">🎙 AI PROMPTER</h1>
        <div className="edit-header-actions">
          <label
            htmlFor="doc-import"
            className={`import-btn${isImporting ? " loading" : ""}`}
            title=".docx · .hwp · .txt 파일을 스크립트로 가져옵니다"
          >
            {isImporting ? "불러오는 중…" : "📄 파일 가져오기"}
          </label>
          <input
            id="doc-import"
            type="file"
            accept=".docx,.hwp,.txt,.md"
            style={{ display: "none" }}
            onChange={handleDocumentImport}
            disabled={isImporting}
          />
          <button className="btn-present" onClick={goToPresent}>
            ▶ 발표 시작
          </button>
        </div>
      </header>

      <div className="edit-body">
        {/* 왼쪽: 스크립트 편집 */}
        <div className="edit-col-script">
          <div className="edit-col-label">스크립트</div>
          {importStatus && <p className="import-status">{importStatus}</p>}
          <textarea
            className="script-input edit-script-textarea"
            style={{
              fontFamily: settings.fontFamily,
              fontWeight: settings.fontWeight,
              fontSize: `${settings.fontSize}px`,
              lineHeight: settings.lineHeight,
              color: settings.textColor,
              background: settings.bgColor,
            }}
            value={script}
            onChange={(e) => setScript(e.target.value)}
            placeholder="여기에 스크립트를 입력하거나 파일을 가져오세요…"
          />
        </div>

        {/* 오른쪽: 설정 패널 */}
        <div className="edit-col-settings">
          {/* 마이크 */}
          <div className="edit-section">
            <label className="field-label">마이크</label>
            <div className="device-select-row">
              <select
                className="device-select"
                value={selectedDeviceIndex ?? ""}
                onChange={(e) => setSelectedDeviceIndex(e.target.value === "" ? null : Number(e.target.value))}
              >
                {audioDevices.length === 0 ? (
                  <option value="">마이크를 찾을 수 없음</option>
                ) : (
                  audioDevices.map((d) => (
                    <option key={d.index} value={d.index}>
                      {d.name}{d.default ? " (기본)" : ""}
                    </option>
                  ))
                )}
              </select>
              <button type="button" className="ghost-button" onClick={() => void loadDevices()}>
                새로고침
              </button>
            </div>
            <div className="mic-test-row">
              <span className="mic-level-label">입력 레벨</span>
              {renderVuMeter()}
            </div>
            <div className="status-line">
              <span>
                {health
                  ? health.device ? `디바이스: ${health.device}` : "백엔드 온라인"
                  : "백엔드 오프라인"}
              </span>
            </div>
          </div>

          {/* 시작 위치 */}
          <div className="edit-section">
            <div className="field-label">시작 위치</div>
            <div className="keyword-row">
              <input
                className="text-field"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && jumpToKeyword()}
                placeholder="키워드로 검색…"
              />
              <button className="action-button" onClick={jumpToKeyword}>이동</button>
            </div>
            <div className="line-advance-row" style={{ marginTop: 10 }}>
              <button
                className={`toggle-button small${autoLineAdvance ? " active" : ""}`}
                type="button"
                onClick={() => setAutoLineAdvance((v) => !v)}
              >
                {autoLineAdvance ? "자동 진행 켜짐" : "자동 진행 꺼짐"}
              </button>
              <span className="line-counter">
                {lineStats.currentNum} / {lineStats.readable || "—"} 줄
              </span>
            </div>
          </div>

          {/* 화면 설정 */}
          <div className="edit-section">
            <div className="field-label">화면 설정</div>
            <label className="slider-row">
              <span>글자 크기</span>
              <input
                type="range" min="24" max="72" value={settings.fontSize}
                onChange={(e) => setSettings((s) => ({ ...s, fontSize: Number(e.target.value) }))}
              />
              <span className="slider-value">{settings.fontSize}px</span>
            </label>
            <label className="slider-row">
              <span>줄 간격</span>
              <input
                type="range" min="1.2" max="2.1" step="0.05" value={settings.lineHeight}
                onChange={(e) => setSettings((s) => ({ ...s, lineHeight: Number(e.target.value) }))}
              />
            </label>
            <label className="slider-row">
              <span>텍스트 폭</span>
              <input
                type="range" min="640" max="1200" step="10" value={settings.contentWidth}
                onChange={(e) => setSettings((s) => ({ ...s, contentWidth: Number(e.target.value) }))}
              />
            </label>
            <div className="font-row">
              <span>글꼴</span>
              <select
                className="device-select"
                value={settings.fontFamily}
                onChange={(e) => setSettings((s) => ({ ...s, fontFamily: e.target.value }))}
              >
                <optgroup label="기본">
                  <option value={DEFAULT_FONT}>기본 (시스템 글꼴)</option>
                  <option value={PAPERLOGY_FONT}>Paperlogy</option>
                </optgroup>
                {localFonts.length > 0 && (
                  <optgroup label="설치된 폰트">
                    {localFonts.map((f) => (
                      <option key={f} value={`'${f}', sans-serif`}>{f}</option>
                    ))}
                  </optgroup>
                )}
              </select>
            </div>
            <div className="font-row">
              <span>굵기</span>
              <select
                className="device-select"
                value={settings.fontWeight}
                onChange={(e) => setSettings((s) => ({ ...s, fontWeight: Number(e.target.value) }))}
              >
                <option value={300}>가늘게</option>
                <option value={400}>보통</option>
                <option value={500}>중간</option>
                <option value={600}>약간 굵게</option>
                <option value={700}>굵게</option>
                <option value={800}>매우 굵게</option>
              </select>
            </div>
            <div className="font-row">
              <span>글자 색</span>
              <input
                type="color"
                className="color-input"
                value={settings.textColor}
                onChange={(e) => setSettings((s) => ({ ...s, textColor: e.target.value }))}
              />
            </div>
            <div className="font-row">
              <span>배경 색</span>
              <input
                type="color"
                className="color-input"
                value={settings.bgColor}
                onChange={(e) => setSettings((s) => ({ ...s, bgColor: e.target.value }))}
              />
            </div>
          </div>

          {error && <p className="error-text">{error}</p>}
        </div>
      </div>
    </div>
  );
}
