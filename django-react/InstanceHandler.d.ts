export type HandlerListener = (state: any) => void;

export type HandlerIndex = {
  [key: string]: InstanceHandler;
};
