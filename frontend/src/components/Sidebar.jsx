import { API_BASE_URL } from "../api/client.js";
import NavIcon from "./NavIcon.jsx";

const NAV_GROUPS = [
  {
    label: "资料库",
    items: [
      { id: "readShelf", label: "已读书架", status: "active" },
      { id: "retrieval", label: "搜索", status: "active" }
    ]
  },
  {
    label: "研究",
    items: [
      { id: "review", label: "审阅队列", status: "soon" }
    ]
  },
  {
    label: "导入",
    items: [
      { id: "importPreview", label: "导入 PDF", status: "active" },
      { id: "importReview", label: "对象审核工作台", status: "active" }
    ]
  },
  {
    label: "系统",
    items: [
      { id: "systemStatus", label: "系统状态", status: "active" },
      { id: "settings", label: "设置", status: "active" }
    ]
  }
];

export { NAV_GROUPS };

const API_STATUS_LABELS = {
  starting: "正在启动",
  connected: "API 已连接",
  unavailable: "API 不可用",
  checking: "重新检查中",
};

export default function Sidebar({ view, onSelectNav, apiStatus, onRecheckApi }) {
  const apiPhase = apiStatus?.phase || "starting";
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brandMark">N</div>
        <div className="brandText">
          <h1>Search</h1>
          <span>本地科研工作台</span>
        </div>
      </div>
      <nav className="navList" aria-label="工作台导航">
        {NAV_GROUPS.map((group) => (
          <div className="navGroup" key={group.label}>
            <div className="navGroupLabel">{group.label}</div>
            {group.items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`navItem ${view === item.id ? "selected" : ""}`}
                aria-current={view === item.id ? "page" : undefined}
                disabled={item.status === "soon"}
                onClick={() => onSelectNav(item)}
              >
                <span className="navItemMain">
                  <span className="navIcon" aria-hidden="true"><NavIcon id={item.id} /></span>
                  <span className="navLabel">{item.label}</span>
                </span>
                {item.status === "soon" && <small className="navSoon">即将开放</small>}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div className="apiBase">
        <span className="apiStatusLine">
          <i className={`status-${apiPhase}`} aria-hidden="true" />
          {API_STATUS_LABELS[apiPhase] || API_STATUS_LABELS.unavailable}
        </span>
        <code>{API_BASE_URL}</code>
        {apiStatus?.errorCode && <small>诊断码：{apiStatus.errorCode}</small>}
        {apiPhase === "unavailable" && (
          <button className="apiRecheckButton" type="button" onClick={onRecheckApi}>
            重新检查
          </button>
        )}
      </div>
    </aside>
  );
}
