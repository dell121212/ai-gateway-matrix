export default function ChannelsPage() {
  return (
    <div>
      <h2>渠道与路由</h2>
      <div className="card">
        <p>
          渠道 Key、优先级、档位、限时优先、健康探测仍使用成熟的经典控制台，避免双份实现漂移。
        </p>
        <p>
          <a className="btn" href="/" target="_blank" rel="noreferrer">
            打开经典渠道台（同端口 /）
          </a>
        </p>
        <p className="muted">专业控制台在 /console，OpenAI 兼容接口在 /v1，端口均为 4000。</p>
      </div>
    </div>
  );
}
