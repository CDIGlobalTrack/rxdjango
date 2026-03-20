export interface Action {
  callId: number;
  action: string;
  params: any[];
}

export interface ActionError {
  code: number;
  message: string;
}

export interface ActionResponse<T> {
  type: 'actionResponse';
  callId: number;
  result?: T;
  error?: ActionError;
}

interface CallPromise<T> {
  resolve: (value: T) => void;
  reject: (reason: ActionError) => void;
}

export type ActionIndex = {
  [callId: number]: CallPromise;
};

export interface WriteMessage {
  type: 'write';
  writeId: number;
  operation: 'save' | 'create' | 'delete';
  instanceType: string;
  instanceId?: number;
  parentType?: string;
  parentId?: number;
  relationName?: string;
  data?: Record<string, unknown>;
}

export interface WriteError {
  code: number;
  message: string;
}

export interface WriteResponse {
  type: 'writeResponse';
  writeId: number;
  success: boolean;
  error?: WriteError;
}

interface WritePromise {
  resolve: () => void;
  reject: (reason: WriteError) => void;
}

export type WriteIndex = {
  [writeId: number]: WritePromise;
};
