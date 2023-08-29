import { useState, useEffect } from 'react';

function useChannelState<T>(channel: StateChannel<T>) {
  const [state, setReactState] = useState<T | undefined>(undefined);

  useEffect(() => {
    return channel.subscribe(setReactState)
  })

}, [channel]);
