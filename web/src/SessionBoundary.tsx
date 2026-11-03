import { Component, type ReactNode } from 'react';

interface Props { children: ReactNode; active: boolean; onReset: () => void }
/** A rendering failure in one tab must leave other analysis sessions usable. */
export class SessionBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <section hidden={!this.props.active} className="workspace-loading" role="alert">
      <h2>This analysis tab could not be displayed</h2>
      <p>Reset its local settings to reopen it. Recorded source data and reviews are retained.</p>
      <button type="button" onClick={this.props.onReset}>Reset tab settings</button>
    </section>;
  }
}
