import { ref, watch, onUnmounted } from 'vue';

export function useSSE(urlRef, onMessage, onError) {
    const sse = ref(null);
    const isConnected = ref(false);

    const close = () => {
        if (sse.value) {
            sse.value.close();
            sse.value = null;
            isConnected.value = false;
        }
    };

    watch(urlRef, (url) => {
        close();
        if (!url) return;

        try {
            const source = new EventSource(url);
            sse.value = source;
            isConnected.value = true;

            source.onmessage = (event) => {
                if (onMessage) {
                    onMessage(event.data);
                }
            };

            source.onerror = (err) => {
                console.error(`Ошибка SSE для ${url}:`, err);
                isConnected.value = false;
                if (onError) {
                    onError(err);
                }
            };
        } catch (e) {
            console.error(`Не удалось инициализировать SSE для ${url}:`, e);
            isConnected.value = false;
        }
    }, { immediate: true });

    onUnmounted(() => {
        close();
    });

    return {
        sse,
        isConnected,
        close
    };
}
