// Memory Leak Test Cases - JavaScript
// This file contains various memory leak patterns for testing

class MemoryLeakExamples {
    constructor() {
        this.cache = new Map();
        this.eventListeners = [];
        this.intervals = [];
        this.timeouts = [];
    }
    
    // setInterval without clearInterval - HIGH RISK
    startIntervalWithoutClear() {
        const intervalId = setInterval(() => {
            const data = new Array(1000).fill(0);
            this.cache.set(Date.now(), data);
        }, 1000);
        // Missing clearInterval(intervalId) - MEMORY LEAK!
    }
    
    // setTimeout without clearTimeout - MEDIUM RISK
    startTimeoutWithoutClear() {
        const timeoutId = setTimeout(() => {
            console.log("This might be cancelled");
        }, 5000);
        // If we need to cancel, missing clearTimeout(timeoutId)
    }
    
    // Event listener without removal - MEDIUM RISK
    addEventListenerWithoutRemoval() {
        const button = document.getElementById('myButton');
        if (button) {
            const handler = () => {
                console.log('Button clicked');
                this.cache.set(Date.now(), new Array(100));
            };
            button.addEventListener('click', handler);
            // Missing removeEventListener - MEMORY LEAK!
        }
    }
    
    // Proper event listener management
    addEventListenerProper() {
        const button = document.getElementById('myButton');
        if (button) {
            const handler = () => {
                console.log('Button clicked');
            };
            button.addEventListener('click', handler);
            
            // Cleanup function
            return () => {
                button.removeEventListener('click', handler);
            };
        }
    }
    
    // Growing cache without cleanup
    addToCache(key, value) {
        this.cache.set(key, value); // Cache grows indefinitely
    }
    
    // Circular reference
    createCircularReference() {
        const obj1 = {};
        const obj2 = {};
        obj1.ref = obj2;
        obj2.ref = obj1; // Circular reference
        return obj1;
    }
    
    // Closure that captures large objects
    createProblematicClosure() {
        const largeData = new Array(1000000).fill('data');
        
        return function() {
            // This closure keeps largeData in memory
            console.log('Closure called');
            // Even though largeData is not used, it's captured
        };
    }
    
    // Detached DOM nodes
    createDetachedNodes() {
        const container = document.createElement('div');
        for (let i = 0; i < 1000; i++) {
            const element = document.createElement('div');
            element.innerHTML = `Element ${i}`;
            container.appendChild(element);
        }
        // container is not added to DOM but elements are referenced
        return container;
    }
    
    // Global variable that grows
    addToGlobalArray(data) {
        if (!window.globalArray) {
            window.globalArray = [];
        }
        window.globalArray.push(data); // Global array grows indefinitely
    }
    
    // Promise that's never resolved/rejected
    createHangingPromise() {
        return new Promise((resolve, reject) => {
            // Never calls resolve or reject - MEMORY LEAK!
            const data = new Array(1000).fill('hanging');
        });
    }
    
    // Web Worker without termination
    startWorkerWithoutTermination() {
        const worker = new Worker('worker.js');
        worker.postMessage({command: 'start'});
        
        worker.onmessage = function(e) {
            console.log('Message from worker:', e.data);
        };
        // Missing worker.terminate() - MEMORY LEAK!
    }
    
    // Infinite loop with allocation (would crash browser)
    infiniteAllocationLoop() {
        while (true) {
            const data = new Array(1000).fill(Math.random());
            this.cache.set(Date.now(), data);
            // No break condition - CRITICAL MEMORY LEAK!
        }
    }
    
    // Observer without disconnect
    startObserverWithoutDisconnect() {
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                const data = new Array(100).fill(mutation.type);
                this.cache.set(Date.now(), data);
            });
        });
        
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
        // Missing observer.disconnect() - MEMORY LEAK!
    }
    
    // Proper cleanup method
    cleanup() {
        // Clear intervals
        this.intervals.forEach(clearInterval);
        this.intervals = [];
        
        // Clear timeouts
        this.timeouts.forEach(clearTimeout);
        this.timeouts = [];
        
        // Clear cache
        this.cache.clear();
        
        // Remove event listeners would need to be done individually
    }
}

// Static cache that grows
const globalCache = new Map();

function addToGlobalCache(key, value) {
    globalCache.set(key, value); // Global cache grows indefinitely
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MemoryLeakExamples;
}