import { Badge } from "@appica/ui-react/badge";
import { CopyButton } from "@appica/ui-react/copy-button";
import PageIntro from "../components/PageIntro";

const advancedModes = [
  { model: "mode-weak", title: "快速", description: "偏向低延迟与免费额度" },
  { model: "mode-mid", title: "均衡", description: "固定使用中等能力模型" },
  { model: "mode-strong", title: "强模型", description: "固定提高复杂任务能力" },
  { model: "mode-elite", title: "顶级", description: "只使用旗舰候选" },
];

export default function ConnectionPage() {
  const apiBase = "http://127.0.0.1:4000/v1";
  return <div className="page-stack">
    <PageIntro title="接入" />
    <section className="connection-focus">
      <div className="connection-focus-copy">
        <Badge variant="success">推荐</Badge>
        <h3>只使用一个地址、一个模型</h3>
        <p>把下面两项填入 Codex、Cline、Roo Code 或任何 OpenAI-compatible 客户端。渠道和模型无需手选。</p>
      </div>
      <div className="connection-values">
        <div><span>Base URL</span><code>{apiBase}</code><CopyButton value={apiBase} label="复制" copiedLabel="已复制" size="sm" /></div>
        <div><span>Model</span><code>auto-route</code><CopyButton value="auto-route" label="复制" copiedLabel="已复制" size="sm" /></div>
      </div>
    </section>
    <section className="auto-route-explainer card">
      <div className="auto-route-node"><strong>你的请求</strong><span>无需判断难度</span></div>
      <span className="route-arrow" aria-hidden="true">→</span>
      <div className="auto-route-brain"><strong>Adaptive</strong><span>质量 · 额度 · 延迟 · 成本</span></div>
      <span className="route-arrow" aria-hidden="true">→</span>
      <div className="auto-route-node"><strong>最佳可用模型</strong><span>失败自动换路</span></div>
    </section>
    <details className="advanced-disclosure card">
      <summary>需要固定能力档位时再展开</summary>
      <div className="compact-mode-grid">
        {advancedModes.map((mode) => <article key={mode.model}><div><strong>{mode.title}</strong><span>{mode.description}</span></div><code>{mode.model}</code><CopyButton value={mode.model} label="复制" copiedLabel="已复制" size="sm" /></article>)}
      </div>
    </details>
  </div>;
}
