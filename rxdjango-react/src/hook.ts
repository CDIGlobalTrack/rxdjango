import { useState, useEffect } from 'react';
import ContextChannel from './ContextChannel';

export const useChannelState = <T>(channel: ContextChannel<T | T[]>) => {
  const [state, setReactState] = useState<T | T[]>();
  const [noConnectionSince, setNoConnectionSince] = useState<Date>();

  useEffect(() => {
    const unsubscribe = channel.subscribe(setReactState, setNoConnectionSince);

    return () => {
      unsubscribe();
    }
  }, []);

  return { state, no_connection_since: noConnectionSince };
}
