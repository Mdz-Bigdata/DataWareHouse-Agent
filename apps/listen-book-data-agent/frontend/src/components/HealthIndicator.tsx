import { useEffect, useState } from 'react';

interface DependencyReport {
  status: string;
  detail?: string;
}

interface ReadyReport {
  status: string;
  dependencies: Record<string, DependencyReport>;
}

type HealthStatus = 'checking' | 'ready' | 'unavailable' | 'unreachable';

const STATUS_LABELS: Record<HealthStatus, string> = {
  checking: '检查中…',
  ready: '服务正常',
  unavailable: '依赖异常',
  unreachable: '无法连接',
};

const DEPENDENCY_LABELS: Record<string, string> = {
  metadata_mysql: '元数据库',
  warehouse_mysql: '数仓',
  qdrant: 'Qdrant',
  elasticsearch: 'Elasticsearch',
  embedding: '向量服务',
};

/** Polls GET /ready and exposes the real dependency health in the header. */
export function HealthIndicator() {
  const [status, setStatus] = useState<HealthStatus>('checking');
  const [report, setReport] = useState<ReadyReport | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const response = await fetch('/ready', { headers: { Accept: 'application/json' } });
        const body = (await response.json()) as ReadyReport;
        if (cancelled) return;
        setReport(body);
        setStatus(response.ok && body.status === 'ready' ? 'ready' : 'unavailable');
      } catch {
        if (!cancelled) setStatus('unreachable');
      }
    };
    void check();
    const timer = window.setInterval(check, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="health">
      <button
        type="button"
        className={`health-toggle health--${status}`}
        aria-expanded={open}
        aria-label={`服务健康状态：${STATUS_LABELS[status]}`}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="health-dot" aria-hidden="true" />
        <span className="health-text">{STATUS_LABELS[status]}</span>
      </button>
      {open && report && (
        <div className="health-panel" role="status">
          <ul>
            {Object.entries(report.dependencies).map(([name, dep]) => (
              <li key={name}>
                <span>{DEPENDENCY_LABELS[name] ?? name}</span>
                <span className={dep.status === 'ok' ? 'health-ok' : 'health-bad'}>
                  {dep.status === 'ok' ? '正常' : `异常${dep.detail ? `（${dep.detail}）` : ''}`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
