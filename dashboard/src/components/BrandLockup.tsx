import { useTheme } from "../context/ThemeContext";

/**
 * FlowBee brand lockup — the official horizontal logo (bee mark + wordmark),
 * used verbatim. The dark-mode variant only recolors the wordmark to ivory so
 * it stays readable on dark surfaces; the bee mark is untouched.
 */
export default function BrandLockup({
  height = 32,
  className,
}: {
  /** Rendered height in px; width follows the logo's 1093:292 ratio. */
  height?: number;
  className?: string;
}) {
  const { isDark } = useTheme();

  return (
    <img
      src={isDark ? "/flowbee-lockup-dark.png" : "/flowbee-lockup-light.png"}
      alt="FlowBee"
      className={className}
      style={{
        height,
        width: "auto",
        maxWidth: "100%",
        objectFit: "contain",
        display: "block",
        flexShrink: 0,
      }}
    />
  );
}
