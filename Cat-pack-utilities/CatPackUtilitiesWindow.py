import glfw
from OpenGL.GL import *
import OpenGL.GL.shaders as shaders
import ctypes
import logging
from logging_config import configure_logging

# Configure logging
configure_logging()

def window_resize(window, width, height):
    glViewport(0, 0, width, height)

def main():
    logging.info("Initializing GLFW")
    # Initialize the library
    if not glfw.init():
        logging.error("GLFW initialization failed")
        return

    logging.info("Creating window")
    # Create a windowed mode window and its OpenGL context
    window = glfw.create_window(1280, 720, "OpenGL Window", None, None)
    if not window:
        logging.error("Window or OpenGL context creation failed")
        glfw.terminate()
        return

    logging.info("Setting window resize callback")
    # Set callbacks
    glfw.set_window_size_callback(window, window_resize)

    logging.info("Making context current")
    # Make the window's context current
    glfw.make_context_current(window)

    # Define the vertices of a rectangle (button)
    vertices = [-0.5, -0.5, 0.0,  # Bottom-left corner
                0.5, -0.5, 0.0,  # Bottom-right corner
                0.5, 0.5, 0.0,   # Top-right corner
                -0.5, 0.5, 0.0]  # Top-left corner

    vertices = (GLfloat * len(vertices))(*vertices)

    # Vertex shader source code
    vertex_shader_src = """
    #version 330 core
    layout (location = 0) in vec3 aPos;
    void main()
    {
        gl_Position = vec4(aPos.x, aPos.y, aPos.z, 1.0);
    }
    """

    # Fragment shader source code
    fragment_shader_src = """
    #version 330 core
    out vec4 FragColor;
    void main()
    {
        FragColor = vec4(1.0f, 0.5f, 0.2f, 1.0f);
    }
    """

    # Compile shaders and link program
    shader = shaders.compileProgram(
        shaders.compileShader(vertex_shader_src, GL_VERTEX_SHADER),
        shaders.compileShader(fragment_shader_src, GL_FRAGMENT_SHADER)
    )
    glUseProgram(shader)

    # Create Vertex Array Object (VAO) and Vertex Buffer Object (VBO)
    VAO = glGenVertexArrays(1)
    VBO = glGenBuffers(1)
    glBindVertexArray(VAO)
    glBindBuffer(GL_ARRAY_BUFFER, VBO)
    glBufferData(GL_ARRAY_BUFFER, len(vertices) * ctypes.sizeof(GLfloat), vertices, GL_STATIC_DRAW)
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 3 * ctypes.sizeof(GLfloat), ctypes.c_void_p(0))
    glEnableVertexAttribArray(0)
    glBindBuffer(GL_ARRAY_BUFFER, 0)
    glBindVertexArray(0)

    # Loop until the user closes the window
    while not glfw.window_should_close(window):
        # Clear the screen
        glClear(GL_COLOR_BUFFER_BIT)

        # Draw the button
        glUseProgram(shader)
        glBindVertexArray(VAO)
        glDrawArrays(GL_QUADS, 0, 4)  # Draw a rectangle (button)

        # Swap front and back buffers
        glfw.swap_buffers(window)

        # Poll for and process events
        glfw.poll_events()

    # Terminate GLFW and cleanup
    logging.info("Closing the application")
    glfw.terminate()


if __name__ == "__main__":
    main()