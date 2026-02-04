import { useEffect, useRef, useState } from "react";

type Props = {
  onSend: (message: string) => void;
  disabled: boolean;
  voiceTranscript: string;
  isListening: boolean;
  isVoiceSupported: boolean;
  onMicToggle: () => void;
  onResetTranscript: () => void;
};

export default function InputBar({
  onSend,
  disabled,
  voiceTranscript,
  isListening,
  isVoiceSupported,
  onMicToggle,
  onResetTranscript,
}: Props) {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const prevListeningRef = useRef(false);

  // When voice transcript changes, update the input
  useEffect(() => {
    if (isListening && voiceTranscript) {
      setInput(voiceTranscript);
    }
  }, [voiceTranscript, isListening]);

  // Auto-submit when listening stops and there's a transcript
  useEffect(() => {
    if (prevListeningRef.current && !isListening && voiceTranscript.trim()) {
      onSend(voiceTranscript.trim());
      setInput("");
      onResetTranscript();
    }
    prevListeningRef.current = isListening;
  }, [isListening, voiceTranscript, onSend, onResetTranscript]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || disabled) return;
    onSend(text);
    setInput("");
    onResetTranscript();
    inputRef.current?.focus();
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="fixed inset-x-0 bottom-0 border-t border-white/10 bg-[#0a0a0f]/90 backdrop-blur-sm p-3 safe-area-bottom"
    >
      <div className="mx-auto flex max-w-2xl items-center gap-2">
        {/* Mic button */}
        {isVoiceSupported && (
          <button
            type="button"
            onClick={onMicToggle}
            disabled={disabled}
            className={`shrink-0 rounded-full p-2.5 transition-colors ${
              isListening
                ? "animate-pulse bg-red-600 text-white"
                : "bg-slate-800 text-slate-400 hover:text-slate-200"
            } disabled:opacity-50`}
            aria-label={isListening ? "Stop listening" : "Start voice input"}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="currentColor"
              className="h-5 w-5"
            >
              <path d="M12 1a4 4 0 0 0-4 4v7a4 4 0 0 0 8 0V5a4 4 0 0 0-4-4Z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2H3v2a9 9 0 0 0 8 8.94V23h2v-2.06A9 9 0 0 0 21 12v-2h-2Z" />
            </svg>
          </button>
        )}

        {/* Text input */}
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={isListening ? "Listening..." : "Ask about a show or movie..."}
          disabled={disabled || isListening}
          className="flex-1 rounded-xl border border-white/10 bg-slate-800/80 px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 outline-none focus:border-indigo-500 disabled:opacity-50"
          autoComplete="off"
        />

        {/* Send button */}
        <button
          type="submit"
          disabled={disabled || !input.trim()}
          className="shrink-0 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Send
        </button>
      </div>
    </form>
  );
}
