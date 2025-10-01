// Test JavaScript file with security vulnerabilities

class VulnerableJS {
    
    // XSS vulnerabilities
    displayUserData(userData) {
        document.innerHTML = userData;  // XSS vulnerability
        document.write(userData);  // XSS vulnerability
        element.outerHTML = userContent;  // XSS vulnerability
    }
    
    // Code injection
    executeUserCode(userCode) {
        eval(userCode);  // Code injection
        setTimeout(userCode, 1000);  // Code injection
        setInterval("alert(" + userInput + ")", 1000);  // Code injection
    }
    
    // Hardcoded credentials
    const API_KEY = "sk_live_1234567890abcdef";  // Hardcoded API key
    const PASSWORD = "admin123";  // Hardcoded password
    
    // Insecure network communication
    fetchData() {
        fetch("http://api.example.com/data");  // Insecure HTTP
    }
    
    // Weak random
    generateId() {
        return Math.random();  // Weak random
    }
    
    // SQL injection (in template literals)
    buildQuery(userId) {
        return `SELECT * FROM users WHERE id = ${userId}`;  // SQL injection potential
    }
    
    // Secure code example
    secureFunction() {
        console.log("This is secure");
        return true;
    }
}