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
