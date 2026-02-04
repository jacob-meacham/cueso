export const API_URL = import.meta.env.VITE_API_URL || "";

export const WS_URL =
  import.meta.env.VITE_WS_URL ||
  `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;

export const SERVICE_COLORS: Record<string, string> = {
  netflix: "#E50914",
  hulu: "#1CE783",
  disney_plus: "#0063E5",
  max: "#002BE7",
  apple_tv_plus: "#A2AAAD",
  amazon_prime: "#00A8E1",
};

export const SERVICE_DISPLAY_NAMES: Record<string, string> = {
  netflix: "Netflix",
  hulu: "Hulu",
  disney_plus: "Disney+",
  max: "Max",
  apple_tv_plus: "Apple TV+",
  amazon_prime: "Prime Video",
};
