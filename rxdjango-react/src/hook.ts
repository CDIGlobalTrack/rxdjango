import { useState, useEffect } from 'react';
import ContextChannel from './ContextChannel';
import { InstanceType } from './ContextChannel.interfaces';

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

  return { state, connected, no_connection_since: noConnectionSince, runtimeState, empty };
};

export const useChannelInstance = <T, Y>(channel: ContextChannel<T> | undefined, instance_type: string, instance_id: number | undefined) => {
  const [instance, setInstance] = useState<Y>();

  useEffect(() => {
    if (!channel || !instance_id) return;
    const handleSetInstance = (i: InstanceType) => setInstance(i as Y);
    const unsubscribe = channel.subscribeInstance(handleSetInstance, instance_id, instance_type);
    return () => {
      unsubscribe();
    }
  }, [channel, instance_id, instance_type]);

  return instance;
};
