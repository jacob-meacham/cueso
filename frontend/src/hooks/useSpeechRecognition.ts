import { useCallback, useEffect, useRef, useState } from "react";

type SpeechRecognitionType = typeof window extends {
  SpeechRecognition: infer T;
}
  ? T
  : unknown;

function getRecognitionCtor(): SpeechRecognitionType | null {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function useSpeechRecognition() {
  const [transcript, setTranscript] = useState("");
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<InstanceType<
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    any
  > | null>(null);
  const isSupported = typeof window !== "undefined" && !!getRecognitionCtor();

  useEffect(() => {
    if (!isSupported) return;

    const Ctor = getRecognitionCtor();
    if (!Ctor) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const recognition = new (Ctor as any)();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: { results: SpeechRecognitionResultList }) => {
      let text = "";
      for (let i = 0; i < event.results.length; i++) {
        const result = event.results[i];
        if (result) {
          text += result[0]?.transcript ?? "";
        }
      }
      setTranscript(text);
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognition.onerror = () => {
      setIsListening(false);
    };

    recognitionRef.current = recognition;

    return () => {
      recognition.abort();
    };
  }, [isSupported]);

  const startListening = useCallback(() => {
    if (recognitionRef.current && !isListening) {
      setTranscript("");
      recognitionRef.current.start();
      setIsListening(true);
    }
  }, [isListening]);

  const stopListening = useCallback(() => {
    if (recognitionRef.current && isListening) {
      recognitionRef.current.stop();
    }
  }, [isListening]);

  const resetTranscript = useCallback(() => {
    setTranscript("");
  }, []);

  return {
    transcript,
    isListening,
    isSupported,
    startListening,
    stopListening,
    resetTranscript,
  };
}
