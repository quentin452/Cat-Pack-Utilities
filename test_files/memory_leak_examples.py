# Memory Leak Test Cases - Python
# This file contains various memory leak patterns for testing

import threading
import time
import urllib.request
from functools import lru_cache

class MemoryLeakExamples:
    
    def __init__(self):
        self.cache = {}
        self.growing_list = []
        self.static_data = []
    
    # File without context manager - HIGH RISK
    def read_file_without_context_manager(self, filename):
        f = open(filename, 'r')
        content = f.read()
        # Missing f.close() - MEMORY LEAK!
        return content
    
    # URL connection without close - MEDIUM RISK
    def fetch_url_without_close(self, url):
        response = urllib.request.urlopen(url)
        data = response.read()
        # Missing response.close() - MEMORY LEAK!
        return data
    
    # Proper file handling with context manager
    def read_file_proper(self, filename):
        with open(filename, 'r') as f:
            return f.read()
        # Automatically closed
    
    # Infinite loop with allocation - CRITICAL
    def infinite_allocation_loop(self):
        while True:
            data = [i for i in range(1000)]
            self.growing_list.extend(data)
            # No break condition, continuous allocation
    
    # Growing collection without cleanup
    def add_to_cache(self, key, value):
        self.cache[key] = value  # Cache grows indefinitely
        self.growing_list.append(value)  # List grows indefinitely
    
    # Thread without join - HIGH RISK
    def start_thread_without_join(self):
        def worker():
            while True:
                data = {}
                data['items'] = [i for i in range(100)]
                time.sleep(1)
        
        thread = threading.Thread(target=worker)
        thread.start()
        # Missing thread.join() - MEMORY LEAK!
    
    # Circular reference
    def create_circular_reference(self):
        self.parent = self
        # Creates circular reference
    
    # LRU cache without size limit - MEDIUM RISK
    @lru_cache()  # No maxsize parameter
    def expensive_computation(self, n):
        return sum(i*i for i in range(n))
    
    # Cache with size limit (good practice)
    @lru_cache(maxsize=128)
    def expensive_computation_limited(self, n):
        return sum(i*i for i in range(n))
    
    # Growing dictionary without cleanup
    def process_data(self, key, data):
        self.cache[key] = data
        # Dictionary grows without any cleanup mechanism
    
    # Static/global data that grows
    global_cache = []
    
    def add_to_global_cache(self, data):
        global global_cache
        global_cache.append(data)  # Global list grows indefinitely

# Example of problematic class with circular references
class Parent:
    def __init__(self):
        self.children = []
    
    def add_child(self, child):
        self.children.append(child)
        child.parent = self  # Potential circular reference

class Child:
    def __init__(self):
        self.parent = None

# Memory leak in generator (if not handled properly)
def infinite_generator():
    data = []
    while True:
        data.append("more data")  # Data accumulates
        yield data[-1]

# Problematic event handler setup
class EventHandler:
    def __init__(self):
        self.callbacks = []
    
    def register_callback(self, callback):
        self.callbacks.append(callback)
        # No mechanism to unregister callbacks
    
    def trigger_event(self):
        for callback in self.callbacks:
            callback()

# Example usage that would cause leaks
if __name__ == "__main__":
    examples = MemoryLeakExamples()
    
    # These would cause memory leaks:
    # examples.read_file_without_context_manager("test.txt")
    # examples.infinite_allocation_loop()
    # examples.start_thread_without_join()
    
    print("Memory leak examples loaded (not executed to avoid actual leaks)")