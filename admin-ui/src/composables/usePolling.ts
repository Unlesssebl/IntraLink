import { onMounted, onUnmounted, ref } from 'vue';

export const usePolling = (fn: () => void | Promise<void>, intervalMs: number = 10000, autoStart: boolean = true) => {
  let timer: any = null;
  const isRunning = ref(false);
  const remainingSec = ref(Math.floor(intervalMs / 1000));
  let countdownTimer: any = null;

  const tick = async () => {
    remainingSec.value = Math.floor(intervalMs / 1000);
    try {
      await fn();
    } catch (e) {
      console.error('Polling error:', e);
    }
  };

  const start = () => {
    stop();
    isRunning.value = true;
    remainingSec.value = Math.floor(intervalMs / 1000);
    tick();

    timer = setInterval(tick, intervalMs);
    countdownTimer = setInterval(() => {
      if (remainingSec.value > 0) {
        remainingSec.value--;
      }
    }, 1000);
  };

  const stop = () => {
    isRunning.value = false;
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    if (countdownTimer) {
      clearInterval(countdownTimer);
      countdownTimer = null;
    }
  };

  if (autoStart) {
    onMounted(() => {
      start();
    });
  }

  onUnmounted(() => {
    stop();
  });

  return {
    start,
    stop,
    isRunning,
    remainingSec,
  };
};
