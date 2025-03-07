import { useState, useEffect } from 'react';
import ContextChannel from './ContextChannel';

export const useChannelState = <T, Y=unknown>(channel: ContextChannel<T>) => {
  const [state, setReactState] = useState<T>();
  const [runtimeState, setRuntimeState] = useState<Y>();
  const [connected, setConnected] = useState<boolean>(false);
  const [empty, setEmpty] = useState<boolean>(false);
  const [noConnectionSince, setNoConnectionSince] = useState<Date>();

  useEffect(() => {
    channel.onConnected = () => {
      setConnected(true);
    };

    channel.onEmpty = () => {
      setEmpty(true);
    }

    const unsubscribe = channel.subscribe(setReactState, setNoConnectionSince);
    const runtimeUnsubscribe = channel.runtimeState === null ? null : channel.subscribeRuntimeState((rs) => setRuntimeState(rs as Y));

    return () => {
      unsubscribe();
      if (runtimeUnsubscribe) runtimeUnsubscribe();
    }
  }, [channel]);
  console.log(empty);
  return { state, connected, no_connection_since: noConnectionSince, runtimeState, empty };
};
