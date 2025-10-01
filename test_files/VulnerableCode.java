// Test Java file with various security vulnerabilities
import java.io.*;
import java.sql.*;
import javax.xml.parsers.*;
import java.security.MessageDigest;
import java.util.Random;

public class VulnerableCode {
    
    // CRITICAL: Java deserialization vulnerability
    public Object deserializeObject(InputStream input) throws Exception {
        ObjectInputStream ois = new ObjectInputStream(input);
        return ois.readObject(); // Dangerous deserialization
    }
    
    // HIGH: SQL Injection vulnerability
    public void getUserData(String userId, Connection conn) throws SQLException {
        Statement stmt = conn.createStatement();
        String query = "SELECT * FROM users WHERE id = " + userId; // SQL injection
        ResultSet rs = stmt.executeQuery(query);
    }
    
    // HIGH: Command injection vulnerability
    public void executeCommand(String userInput) throws IOException {
        Runtime.getRuntime().exec("ping " + userInput); // Command injection
    }
    
    // HIGH: Path traversal vulnerability
    public void readFile(String fileName) throws IOException {
        File file = new File("/uploads/" + fileName); // Path traversal
        FileInputStream fis = new FileInputStream(file);
    }
    
    // HIGH: Hardcoded password
    private static final String PASSWORD = "admin123"; // Hardcoded credential
    private static final String API_KEY = "sk_live_abcd1234567890"; // Hardcoded API key
    
    // MEDIUM: Weak cryptography
    public String hashPassword(String password) throws Exception {
        MessageDigest md = MessageDigest.getInstance("MD5"); // Weak hash
        byte[] hash = md.digest(password.getBytes());
        return new String(hash);
    }
    
    // MEDIUM: Weak random number generation
    public int generateSessionId() {
        Random rand = new Random(); // Weak random
        return rand.nextInt();
    }
    
    // HIGH: XXE vulnerability
    public void parseXML(String xmlData) throws Exception {
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        DocumentBuilder builder = factory.newDocumentBuilder(); // XXE vulnerable
        // Missing secure configuration
    }
    
    // MEDIUM: Reflection abuse
    public Object createInstance(String className) throws Exception {
        Class<?> clazz = Class.forName(className); // Dangerous reflection
        return clazz.newInstance();
    }
    
    // Example of secure code (should not be flagged)
    public void secureCode() {
        // This is fine
        System.out.println("Hello World");
    }
}