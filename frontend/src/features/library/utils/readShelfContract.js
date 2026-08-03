import { createApiContractError } from "../../../shared/api/client.js";

export function normalizeReadShelfPayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw createApiContractError(
      "read_shelf_response_invalid",
      "Read Shelf response must be a JSON object.",
    );
  }
  if (typeof payload.status !== "string" || !Array.isArray(payload.items)) {
    throw createApiContractError(
      "read_shelf_response_invalid",
      "Read Shelf response must contain status and an items array.",
    );
  }
  if (payload.message !== undefined && typeof payload.message !== "string") {
    throw createApiContractError(
      "read_shelf_response_invalid",
      "Read Shelf message must be a string when present.",
    );
  }
  if (payload.status === "not_connected_yet") {
    throw createApiContractError(
      "read_shelf_database_read_failed",
      "Read Shelf backend reported a read failure.",
    );
  }
  if (!new Set(["ok", "empty_library"]).has(payload.status)) {
    throw createApiContractError(
      "read_shelf_response_status_invalid",
      "Read Shelf returned an unsupported status.",
    );
  }
  return {
    status: payload.status === "ok" ? "ready" : "empty",
    items: payload.items,
    message: payload.message || "",
  };
}

export function presentReadShelfError(error) {
  const code = String(error?.code || "api_request_failed");
  const presentations = {
    api_connection_failed: ["已读书架连接失败", "无法连接本地 API。请确认 Search Runtime 已启动。"],
    api_request_timeout: ["已读书架连接超时", "本地 API 未在限定时间内响应。"],
    api_endpoint_not_found: ["已读书架接口不存在", "当前后端未提供已读书架接口，可能与正式前端版本不匹配。"],
    api_method_not_allowed: ["已读书架接口方法不匹配", "当前后端不接受已读书架 GET 请求。"],
    api_internal_error: ["已读书架服务内部错误", "本地 API 处理已读书架请求时发生内部错误。"],
    read_shelf_internal_error: ["已读书架服务内部错误", "本地 API 处理已读书架请求时发生内部错误。"],
    read_shelf_database_read_failed: ["已读书架数据库查询失败", "本地数据库只读查询失败，数据未被修改。"],
    api_response_content_type_invalid: ["已读书架响应格式错误", "本地 API 返回了非 JSON 响应。"],
    api_response_json_invalid: ["已读书架响应格式错误", "本地 API 返回的 JSON 无法解析。"],
    read_shelf_response_invalid: ["已读书架响应格式错误", "响应缺少 status 或数组型 items。"],
    read_shelf_response_status_invalid: ["已读书架响应格式错误", "响应包含不支持的 status。"],
  };
  const [title, message] = presentations[code] || [
    "已读书架暂不可用",
    "本地 API 请求失败。",
  ];
  return { title, message, code };
}
