import { onUnmounted } from 'vue';

export function usePolling(fn, intervalMs, immediate = true) {
    let timer = null;

    const start = () => {
        stop();
        if (immediate) {
            fn();
        }
        timer = setInterval(fn, intervalMs);
    };

    const stop = () => {
        if (timer) {
            clearInterval(timer);
            timer = null;
        }
    };

    start();

    onUnmounted(() => {
        stop();
    });

    return {
        start,
        stop
    };
}
