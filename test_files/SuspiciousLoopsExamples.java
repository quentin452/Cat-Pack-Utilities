// Suspicious Loops Test Cases - Java
// This file contains various suspicious loop patterns for testing

import java.io.*;
import java.util.*;
import java.sql.*;

public class SuspiciousLoopsExamples {
    
    // Infinite loop - CRITICAL
    public void infiniteLoop() {
        while (true) {
            System.out.println("This will run forever!");
            // Missing break condition - CRITICAL ISSUE!
        }
    }
    
    // Infinite for loop - CRITICAL
    public void infiniteForLoop() {
        for (;;) {
            List<String> data = new ArrayList<>();
            data.add("This allocates memory forever");
            // No break condition - CRITICAL ISSUE!
        }
    }
    
    // Memory allocation in loop - HIGH RISK
    public void memoryAllocationLoop() {
        for (int i = 0; i < 1000000; i++) {
            ArrayList<String> list = new ArrayList<>(); // New allocation each iteration
            HashMap<String, Object> map = new HashMap<>(); // More memory allocation
            list.add("Memory leak potential");
            map.put("key", new Object());
        }
    }
    
    // Triple nested loop - HIGH COMPLEXITY
    public void tripleNestedLoop() {
        for (int i = 0; i < 1000; i++) {
            for (int j = 0; j < 1000; j++) {
                for (int k = 0; k < 1000; k++) {
                    // O(n³) complexity - HIGH RISK!
                    System.out.println(i + "," + j + "," + k);
                }
            }
        }
    }
    
    // Quadruple nested loop - CRITICAL COMPLEXITY
    public void quadrupleNestedLoop() {
        for (int i = 0; i < 100; i++) {
            for (int j = 0; j < 100; j++) {
                for (int k = 0; k < 100; k++) {
                    for (int l = 0; l < 100; l++) {
                        // O(n⁴) complexity - CRITICAL ISSUE!
                        processData(i, j, k, l);
                    }
                }
            }
        }
    }
    
    // I/O operations in loop - HIGH RISK
    public void ioInLoop() throws IOException {
        for (int i = 0; i < 1000; i++) {
            FileInputStream fis = new FileInputStream("file" + i + ".txt");
            System.out.println("Reading file " + i); // I/O in loop
            fis.close();
        }
    }
    
    // Database operations in loop - HIGH RISK
    public void databaseInLoop() throws SQLException {
        Connection conn = DriverManager.getConnection("jdbc:mysql://localhost/test");
        for (int i = 0; i < 1000; i++) {
            PreparedStatement stmt = conn.prepareStatement("SELECT * FROM users WHERE id = ?");
            stmt.setInt(1, i);
            ResultSet rs = stmt.executeQuery(); // Database call in loop - HIGH RISK!
            rs.close();
            stmt.close();
        }
        conn.close();
    }
    
    // String concatenation in loop - MEDIUM RISK
    public void stringConcatenationLoop() {
        String result = "";
        for (int i = 0; i < 10000; i++) {
            result += "Item " + i + ", "; // Inefficient string concatenation
        }
        System.out.println(result);
    }
    
    // Better approach with StringBuilder
    public void stringBuilderLoop() {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 10000; i++) {
            sb.append("Item ").append(i).append(", ");
        }
        System.out.println(sb.toString());
    }
    
    // Inefficient collection operations in loop
    public void inefficientCollectionLoop() {
        List<String> list = new ArrayList<>();
        for (int i = 0; i < 1000; i++) {
            list.add("Item " + i);
        }
        
        for (String item : list) {
            if (list.contains("Item 500")) { // O(n) operation in loop
                System.out.println("Found item 500");
            }
        }
    }
    
    // Recursive calls in loop - HIGH RISK
    public void recursiveCallsInLoop() {
        for (int i = 0; i < 100; i++) {
            fibonacci(i); // Recursive call in loop - HIGH RISK!
        }
    }
    
    public int fibonacci(int n) {
        if (n <= 1) return n;
        return fibonacci(n - 1) + fibonacci(n - 2);
    }
    
    // Sleep in loop - MEDIUM RISK
    public void sleepInLoop() throws InterruptedException {
        for (int i = 0; i < 10; i++) {
            Thread.sleep(1000); // Blocking operation in loop
            System.out.println("Iteration " + i);
        }
    }
    
    // Loop with proper exit condition (GOOD EXAMPLE)
    public void properLoopWithBreak() {
        while (true) {
            String input = getUserInput();
            if (input.equals("quit")) {
                break; // Proper exit condition
            }
            processInput(input);
        }
    }
    
    // Helper methods
    private void processData(int i, int j, int k, int l) {
        // Simulate processing
    }
    
    private String getUserInput() {
        return "test";
    }
    
    private void processInput(String input) {
        // Process input
    }
}