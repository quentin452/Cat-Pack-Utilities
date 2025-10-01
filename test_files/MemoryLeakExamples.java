// Memory Leak Test Cases - Java
// This file contains various memory leak patterns for testing

import java.io.*;
import java.util.*;
import java.sql.*;

public class MemoryLeakExamples {
    
    // Static collection - potential memory leak
    private static final List<String> globalCache = new ArrayList<>();
    private static Map<String, Object> staticMap = new HashMap<>();
    
    // Unclosed FileInputStream - CRITICAL
    public void readFileWithoutClosing(String filename) throws IOException {
        FileInputStream fis = new FileInputStream(filename);
        byte[] buffer = new byte[1024];
        fis.read(buffer);
        // Missing fis.close() - MEMORY LEAK!
    }
    
    // Unclosed database connection - CRITICAL
    public void queryDatabaseWithoutClosing() throws SQLException {
        Connection conn = DriverManager.getConnection("jdbc:mysql://localhost/test", "user", "pass");
        Statement stmt = conn.createStatement();
        ResultSet rs = stmt.executeQuery("SELECT * FROM users");
        // Missing conn.close(), stmt.close(), rs.close() - MEMORY LEAK!
    }
    
    // Proper resource management with try-with-resources
    public void readFileProper(String filename) throws IOException {
        try (FileInputStream fis = new FileInputStream(filename)) {
            byte[] buffer = new byte[1024];
            fis.read(buffer);
        } // Automatically closed
    }
    
    // Growing collection without cleanup
    public void addToGlobalCache(String data) {
        globalCache.add(data); // Collection grows indefinitely
        staticMap.put(data, new Object()); // Map grows indefinitely
    }
    
    // Infinite loop with allocation - CRITICAL
    public void infiniteAllocationLoop() {
        while (true) {
            List<String> list = new ArrayList<>();
            list.add("This will cause memory leak");
            // No break condition, continuous allocation
        }
    }
    
    // Thread without proper cleanup - HIGH RISK
    public void startThreadWithoutJoin() {
        Thread worker = new Thread(() -> {
            while (true) {
                try {
                    Thread.sleep(1000);
                    List<String> data = new ArrayList<>();
                } catch (InterruptedException e) {
                    break;
                }
            }
        });
        worker.start();
        // Missing worker.join() or proper lifecycle management
    }
    
    // Circular reference example
    class Parent {
        Child child;
        public void setChild(Child child) {
            this.child = child;
            child.parent = this; // Potential circular reference
        }
    }
    
    class Child {
        Parent parent;
    }
    
    // Cache without eviction policy
    private Map<String, Object> cache = new HashMap<>();
    
    public void cacheData(String key, Object value) {
        cache.put(key, value); // Cache grows without limits
    }
    
    // Finalize without super call
    @Override
    protected void finalize() throws Throwable {
        // Some cleanup code
        System.out.println("Finalizing...");
        // Missing super.finalize() call
    }
    
    // Explicit GC call (anti-pattern)
    public void forceGarbageCollection() {
        System.gc(); // Should be avoided
    }
    
    // Scanner without close
    public void readInputWithoutClosing() {
        Scanner scanner = new Scanner(System.in);
        String input = scanner.nextLine();
        // Missing scanner.close()
    }
}