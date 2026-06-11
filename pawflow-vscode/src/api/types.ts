export interface Attachment {
  filename: string;
  mime_type: string;
  data: string;
}

export interface ReplyTo {
  raw_index: number;
  role: string;
  agent: string;
  text_preview: string;
}

export interface SendMessageRequest {
  message: string;
  conversation_id?: string;
  target_agent?: string;
  attachments?: Attachment[];
  reply_to?: ReplyTo;
  msg_id?: string;
}

export interface AgentResponse {
  conversation_id?: string;
  error?: string;
  [key: string]: any;
}

export interface DisplayMessage {
  type: string;
  content: string;
  source?: { type: string; name: string; llm_service?: string };
  tool_name?: string;
  timestamp?: number;
}

export interface SSEEvent {
  event: string;
  data: Record<string, any>;
}

export interface ConversationInfo {
  conversation_id: string;
  preview?: string;
  message_count?: number;
  updated_at?: number;
}
