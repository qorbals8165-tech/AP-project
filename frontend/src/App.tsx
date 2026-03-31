import { ChangeEvent, CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { estimateProgress, fetchDefaults, fetchHealth, transcribeAudio } from "./api";
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

export default function App() {
  const [script, setScript] = useState(DEFAULT_SCRIPT);
  const [recognizedText, setRecognizedText] = useState("");
  const [keyword, setKeyword] = useState("");
  const [isAutoScroll, setIsAutoScroll] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
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
    fontFamily: "'IBM Plex Sans KR', sans-serif",
  });

  const teleprompterRef = useRef<HTMLDivElement | null>(null);
  const lineRefs = useRef<(HTMLParagraphElement | null)[]>([]);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [isRecording, setIsRecording] = useState(false);

  const lines = useMemo(() => script.split(/\r?\n/), [script]);

  useEffect(() => {
    setCurrentLineIndex(firstNonEmptyLineIndex(lines));
  }, [script]);

  useEffect(() => {
    void fetchDefaults()
      .then(setSettings)
      .catch(() => undefined);

    void fetchHealth()
      .then(setHealth)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!isAutoScroll || !teleprompterRef.current) {
      return;
    }

    const node = teleprompterRef.current;
    const timer = window.setInterval(() => {
      node.scrollBy({ top: settings.scrollSpeed / 4, behavior: "smooth" });
    }, 250);

    return () => window.clearInterval(timer);
  }, [isAutoScroll, settings.scrollSpeed]);

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

  async function syncFromAudioFile(file: File) {
    setError("");
    setIsSyncing(true);

    try {
      const text = await transcribeAudio(file);
      setRecognizedText(text);

      const progress = await estimateProgress(script, text);
      const linesNow = script.split(/\r?\n/);
      const ranges = buildLineRanges(script);

      if (teleprompterRef.current) {
        teleprompterRef.current.scrollTo({
          top: Math.max(progress.matched_index * 0.8, 0),
          behavior: "smooth",
        });
      }

      if (!autoLineAdvance) {
        return;
      }

      setCurrentLineIndex((prev) => {
        let li = firstNonEmptyFrom(linesNow, prev);
        if (li >= linesNow.length) {
          return prev;
        }

        const lineText = linesNow[li];
        if (lineMatchesSpoken(lineText, text)) {
          return nextNonEmptyLineIndex(linesNow, li);
        }

        const progressLine = lineIndexForCharIndex(ranges, progress.matched_index);
        const minConfidence = 0.28;
        if (progress.confidence >= minConfidence && progressLine > li) {
          const jump = firstNonEmptyFrom(linesNow, progressLine);
          return jump < linesNow.length ? jump : prev;
        }

        return prev;
      });
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Audio sync failed");
    } finally {
      setIsSyncing(false);
    }
  }

  async function handleAudioUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    await syncFromAudioFile(file);
    event.target.value = "";
  }

  async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("이 브라우저는 마이크 입력을 지원하지 않습니다.");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        const file = new File([blob], "live-recording.webm", { type: "audio/webm" });
        stream.getTracks().forEach((track) => track.stop());
        void syncFromAudioFile(file);
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
      setError("");
    } catch (recordError) {
      setError(recordError instanceof Error ? recordError.message : "마이크를 시작할 수 없습니다.");
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current = null;
    setIsRecording(false);
  }

  function jumpToKeyword() {
    if (!keyword.trim() || !teleprompterRef.current) {
      return;
    }

    const index = script.toLowerCase().indexOf(keyword.trim().toLowerCase());
    if (index < 0) {
      setError("키워드를 찾지 못했습니다.");
      return;
    }

    setError("");
    const ranges = buildLineRanges(script);
    const lineIdx = lineIndexForCharIndex(ranges, index);
    setCurrentLineIndex(lineIdx);
    teleprompterRef.current.scrollTo({
      top: index * 0.8,
      behavior: "smooth",
    });
  }

  function goToPreviousLine() {
    setCurrentLineIndex((prev) => {
      for (let i = prev - 1; i >= 0; i--) {
        if (lines[i].trim().length > 0) {
          return i;
        }
      }
      return prev;
    });
  }

  function goToNextLineManual() {
    setCurrentLineIndex((prev) => nextNonEmptyLineIndex(lines, prev));
  }

  return (
    <div className={`app-shell ${THEMES[settings.theme].className}`}>
      <aside className="control-panel">
        <div className="hero-card">
          <p className="eyebrow">AI Teleprompter</p>
          <h1>Voice Active Prompter</h1>
          <p className="hero-copy">
            Whisper 기반 음성 인식으로 현재 줄을 읽으면 자동으로 다음 줄로 넘어갑니다. 백엔드가
            켜져 있어야 합니다.
          </p>
        </div>

        <section className="panel-section">
          <label className="field-label" htmlFor="script">
            Script
          </label>
          <textarea
            id="script"
            className="script-input"
            value={script}
            onChange={(event) => setScript(event.target.value)}
          />
        </section>

        <section className="panel-section">
          <label className="field-label" htmlFor="audio">
            Audio Upload
          </label>
          <input id="audio" type="file" accept="audio/*" onChange={handleAudioUpload} />
          <div className="recording-row">
            <button
              className={isRecording ? "toggle-button active" : "toggle-button"}
              onClick={isRecording ? stopRecording : startRecording}
            >
              {isRecording ? "Stop Mic Sync" : "Start Mic Sync"}
            </button>
          </div>
          <div className="status-line">
            <span>{isSyncing ? "Transcribing..." : "Ready for sync"}</span>
            <span>{isRecording ? "Mic recording live" : "Mic idle"}</span>
            <span>{health ? `Device: ${health.device}` : "Backend offline"}</span>
          </div>
          <div className="line-advance-row">
            <button
              className={autoLineAdvance ? "toggle-button active small" : "toggle-button small"}
              type="button"
              onClick={() => setAutoLineAdvance((v) => !v)}
            >
              {autoLineAdvance ? "줄 자동 진행 켜짐" : "줄 자동 진행 꺼짐"}
            </button>
            <span className="line-counter">
              줄 {lineStats.currentNum} / {lineStats.readable || "—"}
            </span>
          </div>
          <p className="hint-text">
            한 줄을 읽은 뒤 녹음을 멈추면 전사가 되고, 현재 줄과 맞으면 다음 줄로 이동합니다.
          </p>
          <div className="line-nav-row">
            <button type="button" className="ghost-button" onClick={goToPreviousLine}>
              이전 줄
            </button>
            <button type="button" className="ghost-button" onClick={goToNextLineManual}>
              다음 줄
            </button>
          </div>
        </section>

        <section className="panel-section">
          <label className="field-label" htmlFor="recognized">
            Recognized Text
          </label>
          <textarea
            id="recognized"
            className="recognized-input"
            value={recognizedText}
            onChange={(event) => setRecognizedText(event.target.value)}
          />
        </section>

        <section className="panel-section inline-controls">
          <div>
            <label className="field-label" htmlFor="keyword">
              Keyword Jump
            </label>
            <input
              id="keyword"
              className="text-field"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="예: GPU 가속"
            />
          </div>
          <button className="action-button" onClick={jumpToKeyword}>
            Jump
          </button>
        </section>

        <section className="panel-section">
          <div className="field-label">Display Settings</div>

          <label className="slider-row">
            <span>Font Size</span>
            <input
              type="range"
              min="24"
              max="72"
              value={settings.fontSize}
              onChange={(event) =>
                setSettings((current) => ({
                  ...current,
                  fontSize: Number(event.target.value),
                }))
              }
            />
          </label>

          <label className="slider-row">
            <span>Line Height</span>
            <input
              type="range"
              min="1.2"
              max="2.1"
              step="0.05"
              value={settings.lineHeight}
              onChange={(event) =>
                setSettings((current) => ({
                  ...current,
                  lineHeight: Number(event.target.value),
                }))
              }
            />
          </label>

          <label className="slider-row">
            <span>Scroll Speed</span>
            <input
              type="range"
              min="8"
              max="80"
              value={settings.scrollSpeed}
              onChange={(event) =>
                setSettings((current) => ({
                  ...current,
                  scrollSpeed: Number(event.target.value),
                }))
              }
            />
          </label>

          <label className="slider-row">
            <span>Content Width</span>
            <input
              type="range"
              min="640"
              max="1100"
              step="10"
              value={settings.contentWidth}
              onChange={(event) =>
                setSettings((current) => ({
                  ...current,
                  contentWidth: Number(event.target.value),
                }))
              }
            />
          </label>

          <div className="theme-row">
            {(Object.keys(THEMES) as ThemeName[]).map((themeName) => (
              <button
                key={themeName}
                className={themeName === settings.theme ? "theme-pill active" : "theme-pill"}
                onClick={() =>
                  setSettings((current) => ({
                    ...current,
                    theme: themeName,
                  }))
                }
              >
                {THEMES[themeName].label}
              </button>
            ))}
          </div>

          <button
            className={isAutoScroll ? "toggle-button active" : "toggle-button"}
            onClick={() => setIsAutoScroll((current) => !current)}
          >
            {isAutoScroll ? "Auto Scroll On" : "Auto Scroll Off"}
          </button>
        </section>

        {error ? <p className="error-text">{error}</p> : null}
      </aside>

      <main className="display-stage">
        <div className="stage-header">
          <div>
            <p className="eyebrow">Live Display</p>
            <h2>실시간 텔레프롬프터</h2>
          </div>
          <div className="telemetry">
            <span>{health ? `Model: ${health.model_size}` : "Model: unknown"}</span>
            <span>{recognizedText ? "Speech synced" : "Waiting for speech"}</span>
          </div>
        </div>

        <div
          ref={teleprompterRef}
          className="teleprompter-frame"
          style={
            {
              "--font-size": `${settings.fontSize}px`,
              "--line-height": String(settings.lineHeight),
              "--content-width": `${settings.contentWidth}px`,
              "--font-family": settings.fontFamily,
            } as CSSProperties
          }
        >
          <div className="teleprompter-gradient top" />
          <div className="teleprompter-content">
            {lines.map((line, index) => {
              const trimmed = line.trim().length > 0;
              const isCurrent = index === currentLineIndex && trimmed;
              const isPast = trimmed && index < currentLineIndex;
              const lineClass = !trimmed
                ? "prompter-line line-empty"
                : isCurrent
                  ? "prompter-line line-current"
                  : isPast
                    ? "prompter-line line-done"
                    : "prompter-line line-upcoming";

              return (
                <p
                  key={`line-${index}`}
                  ref={(el) => {
                    lineRefs.current[index] = el;
                  }}
                  className={lineClass}
                >
                  {line || " "}
                </p>
              );
            })}
          </div>
          <div className="read-guide" />
          <div className="teleprompter-gradient bottom" />
        </div>
      </main>
    </div>
  );
}
