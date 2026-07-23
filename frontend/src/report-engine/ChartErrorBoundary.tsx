// ChartErrorBoundary — the app's object-level error boundary.
//
// Before this existed, ANY exception thrown while rendering a single report
// object (a malformed chart shape, a non-array `rows`, a bad spec) propagated to
// the React root and unmounted the whole SPA — a blank Golden tab. This contains
// the blast radius to one card: the failed object shows a fallback, the rest of
// the report renders normally. `resetKey` lets a re-authored object recover
// without a full remount (a boundary otherwise stays failed until unmounted).
import { Component, ErrorInfo, ReactNode } from "react";

interface Props {
  label?: string;
  resetKey?: string | number;
  children: ReactNode;
}
interface State {
  failed: boolean;
  key?: string | number;
}

export class ChartErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, key: this.props.resetKey };

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true };
  }

  // Clear the failed state when the object being rendered changes (e.g. the
  // curator re-runs the build and `rows` becomes a real array again).
  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey !== state.key) {
      return { failed: false, key: props.resetKey };
    }
    return null;
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // A breadcrumb for debugging; the fallback card is what the user sees.
    console.error(`[object:${this.props.label ?? "?"}] failed to render`, error, info);
  }

  render(): ReactNode {
    if (this.state.failed) {
      return (
        <div
          role="note"
          style={{
            padding: "14px 16px",
            fontSize: 13,
            color: "var(--muted, #9aa4bb)",
            border: "1px solid var(--border, #242b3d)",
            borderRadius: 8,
            background: "var(--panel-2, #171c2b)",
          }}
        >
          ⚠ This {this.props.label ?? "object"} couldn't render.
        </div>
      );
    }
    return this.props.children;
  }
}
