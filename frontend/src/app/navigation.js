import { useMemo, useState } from "react";

export function useAppNavigation({ clearSelection, importPreviewSelection, importPreviewState, setSourceTrace, initialView = "readShelf" }) {
  const [view, setView] = useState(initialView);
  const [returnView, setReturnView] = useState("readShelf");

  const currentTitle = useMemo(() => {
    if (view === "readShelf") return "已读书架";
    if (view === "workspace") return "Research Workspace";
    if (view === "search") return "资料库搜索";
    if (view === "retrieval") return "本地证据检索";
    if (view === "document") return "文档详情";
    if (view === "evidence") return "证据详情";
    if (view === "object") return "对象详情";
    if (view === "importPreview") return "导入 PDF";
    if (view === "importReview") return "对象审核工作台";
    if (view === "systemStatus") return "系统状态";
    if (view === "settings") return "设置";
    return "工作台";
  }, [view]);

  function selectNav(item) {
    if (item.status === "soon") return;
    if (item.id === "readShelf" || item.id === "search") {
      clearSelection();
    }
    if (item.id === "importPreview") {
      setSourceTrace(importPreviewSelection(importPreviewState));
    }
    if (item.id === "importReview") {
      clearSelection();
    }
    setView(item.id);
  }

  function goBackFromDetail() {
    if (returnView === "readShelf" || returnView === "search" || returnView === "importPreview" || returnView === "importReview") {
      clearSelection();
    }
    setView(returnView);
  }

  return {
    view,
    setView,
    returnView,
    setReturnView,
    currentTitle,
    selectNav,
    goBackFromDetail,
  };
}
