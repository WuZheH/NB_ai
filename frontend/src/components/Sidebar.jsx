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
      { id: "workspace", label: "Research Workspace", status: "active" },
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
    items: [{ id: "settings", label: "设置", status: "soon" }]
  }
];

export { NAV_GROUPS };

export default function Sidebar({ view, onSelectNav }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brandMark">N</div>
        <div className="brandText">
          <h1>NOTEBOOK_AI</h1>
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
        <span><i aria-hidden="true" /> API 已连接</span>
        <code>{API_BASE_URL}</code>
      </div>
    </aside>
  );
}
